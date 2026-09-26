from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import insert, or_, select
from sqlalchemy.orm import Session

from app.constants import UTM_SOURCE_MAX_LENGTH, FollowAction, FollowEventSource
from app.db import FollowEvent, User, UserAttribution, user_following_table
from app.logging import logger
from app.schemas import RegistrationAttributionSchema


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_user_handler(user: User):
    logger.info(
        'Зарегистрирован новый пользователь: firebase_uid={firebase_uid}',
        firebase_uid=user.firebase_uid,
    )


def _resolve_referrer_id(
    db: Session, user: User, raw_referrer_id: str | None
) -> UUID | None:
    """Провалидировать сырую реф-метку из ссылки (best-effort).

    Возвращает id существующего пользователя-реферера либо `None`, если метка
    отсутствует, синтаксически битая, указывает на несуществующего юзера или на
    самого регистрирующегося (self-referral). Ничего не поднимает — регистрацию
    метка не валит.
    """
    if not raw_referrer_id:
        return None
    try:
        referrer_id = UUID(raw_referrer_id)
    except ValueError:
        return None
    if referrer_id == user.id:
        # self-referral игнорируем
        return None
    exists = db.scalars(select(User.id).where(User.id == referrer_id)).one_or_none()
    return referrer_id if exists is not None else None


def save_registration_attribution(
    db: Session,
    user: User,
    attribution: RegistrationAttributionSchema | None,
) -> None:
    """Зафиксировать first-touch атрибуцию для только что созданного юзера.

    Best-effort: любые проблемы логируются и не всплывают — регистрация уже
    состоялась и метка её не роняет. Строка не пишется для чистого органика
    (нет ни валидного реферера, ни канала). Вызывать только для нового юзера
    (`is_new_user`) — для повторного логина атрибуция игнорируется целиком.
    """
    # id фиксируем заранее: после провалившегося commit сессия деактивна и ленивое
    # обращение к user.id из except-ветки само бросило бы исключение.
    user_id = user.id
    if attribution is None:
        # Слепая зона: неотличимо от «клиент прислал пустой attribution», см. ниже.
        logger.info(
            'Регистрация user_id={user_id}: attribution не передан клиентом',
            user_id=user_id,
        )
        return
    try:
        referrer_id = _resolve_referrer_id(db, user, attribution.referrer_id)
        # Канал не ограничен по длине на проводе — молча усекаем до лимита;
        # пустую строку нормализуем в None.
        utm_source = (attribution.utm_source or '')[:UTM_SOURCE_MAX_LENGTH] or None
        if referrer_id is None and utm_source is None:
            # чистый органик — фиксировать нечего
            logger.info(
                'Регистрация user_id={user_id}: attribution передан, но пуст '
                '(raw_referrer_id={raw_referrer_id!r}, utm_source={raw_utm!r})',
                user_id=user_id,
                raw_referrer_id=attribution.referrer_id,
                raw_utm=attribution.utm_source,
            )
            return
        db.add(
            UserAttribution(
                user_id=user_id,
                referrer_id=referrer_id,
                utm_source=utm_source,
            )
        )
        db.commit()
        logger.info(
            'Регистрация user_id={user_id}: атрибуция сохранена '
            '(referrer_id={referrer_id}, utm_source={utm_source})',
            user_id=user_id,
            referrer_id=referrer_id,
            utm_source=utm_source,
        )
    except Exception as exc:
        # атрибуция — best-effort, регистрацию не валим
        logger.warning(
            'Не удалось сохранить атрибуцию регистрации user_id={user_id}: {exc}',
            user_id=user_id,
            exc=exc,
        )
        db.rollback()


def create_invite_mutual_follow(
    db: Session,
    user: User,
    attribution: RegistrationAttributionSchema | None,
) -> UUID | None:
    """Регистрация по инвайт-ссылке: подписать новичка и пригласившего друг на
    друга (фича 0024). Вызывать только для нового юзера (first-touch).

    Пригласивший — та же метка, что у атрибуции 0003, с теми же правилами:
    битая, несуществующая (в т.ч. удалённый юзер) или self-метка — рёбер нет.
    Возвращает id пригласившего, если теперь оба подписаны друг на друга, иначе
    `None` — это `mutual_follow_user_id` ответа auth.
    """
    if attribution is None:
        return None
    referrer_id = _resolve_referrer_id(db, user, attribution.referrer_id)
    if referrer_id is None:
        return None
    return follow_each_other_by_invite(db, user, referrer_id)


def follow_each_other_by_invite(
    db: Session, newbie: User, inviter_id: UUID
) -> UUID | None:
    """Создать недостающие рёбра новичок ⇄ пригласивший, best-effort.

    Существующее ребро не трогаем, на каждое созданное — событие лога графа с
    `source=invite`. Пуш пригласившему (`INVITE_JOINED`) шлёт крон по событию
    «пригласивший → новичок»: его нет, если эта подписка уже была, — тогда нет
    и пуша. Событие «новичок → пригласивший» сразу помечено отправленным: оно не
    должно дать пригласившему второй пуш «новый подписчик» про то же самое.
    Любая ошибка БД откатывается и логируется — регистрацию не валит.
    """
    newbie_id = newbie.id
    try:
        existing = set(
            db.execute(
                select(
                    user_following_table.c.follower_id,
                    user_following_table.c.followed_id,
                ).where(
                    or_(
                        (user_following_table.c.follower_id == newbie_id)
                        & (user_following_table.c.followed_id == inviter_id),
                        (user_following_table.c.follower_id == inviter_id)
                        & (user_following_table.c.followed_id == newbie_id),
                    )
                )
            ).tuples()
        )
        for follower_id, followed_id in (
            (newbie_id, inviter_id),
            (inviter_id, newbie_id),
        ):
            if (follower_id, followed_id) in existing:
                continue
            db.execute(
                insert(user_following_table).values(
                    follower_id=follower_id, followed_id=followed_id
                )
            )
            db.add(
                FollowEvent(
                    actor_id=follower_id,
                    target_id=followed_id,
                    action=FollowAction.follow,
                    source=FollowEventSource.invite,
                    is_notification_sent=follower_id == newbie_id,
                )
            )
        db.commit()
    except Exception as exc:
        logger.error(
            'Не удалось подписать новичка {newbie_id} и пригласившего '
            '{inviter_id} друг на друга: {exc}',
            newbie_id=newbie_id,
            inviter_id=inviter_id,
            exc=exc,
        )
        db.rollback()
        return None
    # Рёбра вставлены мимо relationship — сбрасываем закэшированные списки.
    db.expire(newbie)
    return inviter_id
