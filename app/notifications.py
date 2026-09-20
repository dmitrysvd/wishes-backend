from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update

from app.constants import (
    WISH_CREATION_PUSH_DELAY,
    WISH_CREATION_PUSH_HOURS_UTC,
    WISH_CREATION_PUSH_MIN_INTERVAL,
    FollowAction,
    Gender,
)
from app.db import FollowEvent, PushReason, PushSendingLog, SessionLocal, User, Wish
from app.firebase import send_push
from app.logging import logger
from app.main import get_user_deep_link
from app.utils import utc_now


def send_reservation_notifincations():
    with SessionLocal() as db:
        users_with_reserved_wishes_q = select(User).where(
            User.wishes.any(
                Wish.reserved_by_id.is_not(None)
                & ~Wish.is_reservation_notification_sent
            ),
            # Фильтр по установкам — в SQL: дальше юзеры отвязаны от сессии.
            User.can_receive_push,
        )
        users_to_send_pushes = list(db.scalars(users_with_reserved_wishes_q))
    user_ids_to_send_pushes = {user.id for user in users_to_send_pushes}
    with SessionLocal() as db:
        db.execute(
            update(Wish)
            .where(
                Wish.id.in_(
                    select(Wish.id)
                    .join(Wish.user)
                    .where(User.id.in_(user_ids_to_send_pushes))
                )
            )
            .values(is_reservation_notification_sent=True)
        )
        db.commit()
    # Один пуш на владельца за прогон, сколько бы хотелок ни зарезервировали;
    # резервировавших может быть несколько — виновник не указывается.
    send_push(
        target_users=users_to_send_pushes,
        title='Кто-то хочет сделать Вам подарок!',
        body='Одно из ваших желаний было зарезервировано',
        reason=PushReason.RESERVATION,
    )


def send_wish_creation_notifications(now: datetime | None = None) -> None:
    """Отправить подписчикам пуш о новых хотелках автора.

    Хотелка «созрела», когда старше `WISH_CREATION_PUSH_DELAY`. Вне
    `WISH_CREATION_PUSH_HOURS_UTC` прогон ничего не помечает и не шлёт — созревшие
    хотелки уйдут первым прогоном в окне. Пара (подписчик, автор) получает не
    больше одного пуша за `WISH_CREATION_PUSH_MIN_INTERVAL` — по `PushSendingLog`.
    `now` — для тестов окна и интервала; в проде — текущее UTC.
    """
    now = now or utc_now()
    if now.hour not in WISH_CREATION_PUSH_HOURS_UTC:
        logger.info('Пуши о новых хотелках вне окна отправки, час UTC {h}', h=now.hour)
        return
    created_not_later_than = now - WISH_CREATION_PUSH_DELAY
    # `sent_at` в логе — naive UTC (`datetime.now()` в `send_push`).
    interval_start = (now - WISH_CREATION_PUSH_MIN_INTERVAL).replace(tzinfo=None)
    with SessionLocal() as db:
        wishes_filter_cond = ~Wish.is_creation_notification_sent & (
            Wish.created_at < created_not_later_than
        )
        # DISTINCT по всей строке юзера невозможен (JSON-колонка) — по id.
        users_q = select(User).where(
            User.id.in_(select(Wish.user_id).where(wishes_filter_cond))
        )
        users_with_new_wishes = db.scalars(users_q).all()
        db.execute(
            update(Wish)
            .where(wishes_filter_cond)
            .values(is_creation_notification_sent=True)
        )
        db.commit()
        for user in users_with_new_wishes:
            recently_notified = set(
                db.scalars(
                    select(PushSendingLog.target_user_id).where(
                        PushSendingLog.reason == PushReason.WISH_CREATION,
                        PushSendingLog.reason_user_id == user.id,
                        PushSendingLog.sent_at > interval_start,
                    )
                )
            )
            followers_to_send_push = [
                follower
                for follower in user.followed_by
                if follower.can_receive_push and follower.id not in recently_notified
            ]
            if followers_to_send_push:
                logger.info(
                    'Отправляются сообщения о создании хотелок: '
                    'source={user_id}, dest={dest}',
                    user_id=user.id,
                    dest=[str(user.id) for user in followers_to_send_push],
                )
                verb = 'обновила' if user.gender == Gender.female else 'обновил'
                send_push(
                    target_users=followers_to_send_push,
                    title=f'{user.display_name} {verb} список желаний',
                    body=f'Узнайте, что {user.display_name} хочет получить в подарок',
                    reason=PushReason.WISH_CREATION,
                    reason_user=user,
                    link=get_user_deep_link(user),
                )


def send_new_follower_notifications():
    """Отправить юзерам один пуш за все новые подписки с прошлого прогона.

    Пуш на каждое событие подписки давал N пушей за N подписок (единственный
    пуш без дедупа). Теперь события собираются за прогон: у одного подписчика —
    его имя и ссылка на его список, у нескольких — имя первого и счётчик, ссылка
    на свой список. Считаем только тех, кто к моменту прогона всё ещё подписан:
    подписался-и-отписался за час — не событие для пуша.
    """
    with SessionLocal() as db:
        pending_cond = (FollowEvent.action == FollowAction.follow) & (
            ~FollowEvent.is_notification_sent
        )
        events = db.scalars(
            select(FollowEvent).where(pending_cond).order_by(FollowEvent.created_at)
        ).all()
        db.execute(
            update(FollowEvent).where(pending_cond).values(is_notification_sent=True)
        )
        db.commit()
        events_by_target: dict[UUID, list[FollowEvent]] = {}
        for event in events:
            events_by_target.setdefault(event.target_id, []).append(event)
        for target_id, target_events in events_by_target.items():
            target = db.get(User, target_id)
            if target is None or not target.can_receive_push:
                continue
            still_following_ids = {follower.id for follower in target.followed_by}
            followers = [
                db.get(User, actor_id)
                for actor_id in dict.fromkeys(e.actor_id for e in target_events)
                if actor_id in still_following_ids
            ]
            followers = [follower for follower in followers if follower is not None]
            if not followers:
                continue
            first = followers[0]
            if len(followers) == 1:
                body = f'На вас подписался {first.display_name}'
                link = get_user_deep_link(first)
            else:
                body = (
                    f'На вас подписались {first.display_name} '
                    f'и ещё {len(followers) - 1}'
                )
                link = get_user_deep_link(target)
            send_push(
                target_users=[target],
                title='У вас новый подписчик',
                body=body,
                reason=PushReason.NEW_FOLLOWER,
                reason_user=first,
                link=link,
            )
