"""dev/test-байпас аутентификации (фича 0009).

Материализует детерминированных сид-юзеров и собирает bearer, который принимает
`get_current_user`. Токен выдаётся/принимается ТОЛЬКО для сид-юзеров (`is_test`),
поэтому байпас безопасен даже в проде-подобной среде: утёкший секрет не даёт
войти в реальный аккаунт. Включённость гейтится наличием `TEST_AUTH_SECRET`.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import (
    FollowAction,
    FollowEventSource,
    Gender,
    PriceObservationStatus,
    PriceSource,
    RecommendationCategory,
    TestPersona,
)
from app.db import (
    FollowEvent,
    PushInstallation,
    User,
    Wish,
    WishPriceObservation,
    WishRecommendation,
)
from app.helpers.price_watch import (
    ProductObservation,
    WbPriceSchema,
    record_fresh_observation,
)
from app.helpers.store_price import set_manual_price
from app.utils import utc_now

# WB-ссылки сид-хотелок rich: артикулы вымышленные, в магазин сид не ходит —
# наблюдения пишутся в таблицу прибора напрямую (0010/0011/0013 без сети).
_WB = 'https://www.wildberries.ru/catalog/{sku}/detail.aspx'
_RICH_STORE_WISHES = (
    # (название, ссылка, ручная цена | None, наблюдения (дни назад, статус, ₽, «от»))
    (
        'Кроссовки для бега',
        _WB.format(sku=900000001),
        None,
        (
            (1, PriceObservationStatus.ok, 3000, True),
            (0, PriceObservationStatus.ok, 2700, True),
        ),
    ),
    (
        'Рюкзак городской',
        _WB.format(sku=900000002) + '?size=910000002',
        None,
        (
            (1, PriceObservationStatus.ok, 2999, False),
            (0, PriceObservationStatus.sold_out, None, False),
        ),
    ),
    (
        'Термокружка',
        _WB.format(sku=900000003),
        None,
        (
            (1, PriceObservationStatus.ok, 1490, False),
            (0, PriceObservationStatus.gone, None, False),
        ),
    ),
    ('Наушники', _WB.format(sku=900000004), 3500, ()),
)

# Стабильные идентити: firebase_uid — ключ идемпотентного get-or-create.
_RICH_UID = 'test-persona-rich'
_EMPTY_UID = 'test-persona-empty'


@dataclass(frozen=True)
class _RichFriend:
    uid: str
    vk_id: str
    first_name: str
    last_name: str
    bday: date
    gender: Gender
    followed: bool  # False — остаётся в «возможных друзьях» (VK-сидинг)


# Друзья «богатой» персоны. ДР — фиксированные календарные даты (НЕ относительные
# к «сегодня»): бёрздей-радар сортирует по ближайшему наступлению и всегда непуст,
# а ассерты не плывут между прогонами. Попадание конкретного друга в окно
# пуш-троттлинга не гарантируется — гарантируется лишь непустой отсортированный список.
_RICH_FRIENDS = [
    _RichFriend(
        'test-friend-1',
        '2100000001',
        'Аня',
        'Тестовая',
        date(1992, 1, 15),
        Gender.female,
        followed=True,
    ),
    _RichFriend(
        'test-friend-2',
        '2100000002',
        'Борис',
        'Тестов',
        date(1988, 6, 1),
        Gender.male,
        followed=True,
    ),
    _RichFriend(
        'test-friend-3',
        '2100000003',
        'Вера',
        'Тестова',
        date(1995, 12, 20),
        Gender.female,
        followed=False,
    ),
]


def build_test_token(user: User) -> str:
    """Собрать bearer сид-юзера в формате, который принимает `get_current_user`.

    Вызывается только из эндпоинта, доступного лишь при сконфигуренном секрете,
    поэтому `TEST_AUTH_SECRET` здесь не `None`.
    """
    return f'{settings.TEST_AUTH_SECRET}:{user.id}'


def get_or_create_test_user(db: Session, persona: TestPersona) -> User:
    """Найти или детерминированно создать сид-юзера персоны (идемпотентно).

    Первый вызов материализует юзера (и для `rich` — обвязку: друзей-с-ДР,
    подписки, желания, резерв); повторный — находит по firebase_uid и ничего не
    мутирует.
    """
    if persona == TestPersona.rich:
        user = _get_or_create_rich(db)
        _ensure_rich_store_wishes(db, user)
        _ensure_rich_graph_entry(db, user)
        _ensure_recommendations(db)
        return user
    return _get_or_create_empty(db)


def _find_test_user(db: Session, firebase_uid: str) -> User | None:
    return db.execute(
        select(User).where(User.firebase_uid == firebase_uid)
    ).scalar_one_or_none()


def _new_test_user(
    firebase_uid: str,
    display_name: str,
    *,
    vk_id: str | None = None,
    birth_date: date | None = None,
    gender: Gender | None = None,
) -> User:
    now = utc_now()
    return User(
        display_name=display_name,
        firebase_uid=firebase_uid,
        email=f'{firebase_uid}@test.hotelki.pro',
        vk_id=vk_id,
        birth_date=birth_date,
        gender=gender,
        is_test=True,
        registered_at=now,
        last_login_at=now,
    )


def _get_or_create_empty(db: Session) -> User:
    user = _find_test_user(db, _EMPTY_UID)
    if user:
        return user
    # Пустая персона: без VK, без друзей, желаний и подписок — для пустых состояний.
    user = _new_test_user(_EMPTY_UID, 'Эмпти Тестов')
    db.add(user)
    db.commit()
    return user


def _get_or_create_rich(db: Session) -> User:
    user = _find_test_user(db, _RICH_UID)
    if user:
        return user

    user = _new_test_user(
        _RICH_UID,
        'Рич Тестов',
        vk_id='2000000001',
        birth_date=date(1990, 3, 14),
        gender=Gender.male,
    )
    # VK-друзья: и как сырые VK-данные (для «возможных друзей»), и как реальные
    # сид-аккаунты с ДР (для радара/подписок).
    user.vk_friends_data = [
        {
            'id': int(friend.vk_id),
            'first_name': friend.first_name,
            'last_name': friend.last_name,
        }
        for friend in _RICH_FRIENDS
    ]
    db.add(user)

    friends: list[User] = []
    for friend in _RICH_FRIENDS:
        friend_user = _new_test_user(
            friend.uid,
            f'{friend.first_name} {friend.last_name}',
            vk_id=friend.vk_id,
            birth_date=friend.bday,
            gender=friend.gender,
        )
        db.add(friend_user)
        friends.append(friend_user)
        if friend.followed:
            user.follows.append(friend_user)

    # Один друг подписан на богатого юзера — чтобы список подписчиков был непуст.
    friends[0].follows.append(user)

    # Свои желания + резерв чужого: непустой список и непустой раздел
    # «зарезервировано». Через relationship, а не FK-id: id генерится на flush.
    user.wishes.append(Wish(name='Механическая клавиатура'))
    user.wishes.append(Wish(name='Кофемолка'))
    reserved = Wish(name='Настольная лампа')
    friends[1].wishes.append(reserved)
    user.reserved_wishes.append(reserved)

    # Две установки (0016): старый клиент (только токен) и новый (FID + токен) —
    # оба состояния адресов; детерминированно, без сети. В FCM такие адреса
    # не существуют — стенд шлёт с `IS_DEBUG` (dry-run), до FCM не доходит.
    user.push_installations.append(
        PushInstallation(push_token='test-rich-token-legacy')
    )
    user.push_installations.append(
        PushInstallation(fid='test-rich-fid', push_token='test-rich-fid:token')
    )

    db.commit()
    return user


# Вход в граф (0024): новичок, зарегистрированный по инвайт-ссылке rich (взаимные
# подписки с `source=invite`), и подписчик, на которого rich не подписан в ответ.
# Вместе с «Аней» (взаимная обычная) список подписчиков rich — со смешанными
# `followed_by_me`, а профиль Дины — с «Подписаться в ответ».
_RICH_INVITEE_UID = 'test-invitee-rich'
_RICH_FOLLOWER_UID = 'test-follower-no-back'


def _ensure_rich_graph_entry(db: Session, user: User) -> None:
    """Состояния графа 0024 у rich, дописываются и уже существующему rich (стенд),
    идемпотентно по firebase_uid."""
    if _find_test_user(db, _RICH_INVITEE_UID) is None:
        invitee = _new_test_user(_RICH_INVITEE_UID, 'Гоша Новичков', gender=Gender.male)
        db.add(invitee)
        user.follows.append(invitee)
        invitee.follows.append(user)
        db.flush()
        # Как у настоящей регистрации по инвайту; пуш уже «отправлен» — сид не
        # должен порождать пушей на стенде.
        db.add_all(
            FollowEvent(
                actor_id=actor.id,
                target_id=target.id,
                action=FollowAction.follow,
                source=FollowEventSource.invite,
                is_notification_sent=True,
            )
            for actor, target in ((user, invitee), (invitee, user))
        )
    if _find_test_user(db, _RICH_FOLLOWER_UID) is None:
        follower = _new_test_user(
            _RICH_FOLLOWER_UID, 'Дина Подписчикова', gender=Gender.female
        )
        db.add(follower)
        follower.follows.append(user)
    db.commit()


def _ensure_rich_store_wishes(db: Session, user: User) -> None:
    """WB-хотелки rich во всех состояниях склада (0011): «от» в наличии,
    распродано, исчез, ручная цена. Наблюдения датируются относительно «сегодня»
    (свежее + вчерашняя история под триггеры 0013) и ПЕРЕСЧИТЫВАЮТСЯ при каждом
    вызове: e2e видит одну и ту же картину независимо от дня. Хотелки
    дописываются и уже существующему rich (стенд), идемпотентно по ссылке."""
    by_link = {wish.link: wish for wish in user.wishes}
    now = utc_now()
    for name, link, manual_price, observations in _RICH_STORE_WISHES:
        wish = by_link.get(link)
        if wish is None:
            wish = Wish(name=name, link=link)
            user.wishes.append(wish)
            db.flush()
        if manual_price is not None:
            set_manual_price(wish, manual_price)
            continue
        wish.price_source = PriceSource.shop
        # Старый ряд — под снос: даты «вчера/сегодня» должны быть свежими.
        db.execute(
            delete(WishPriceObservation).where(WishPriceObservation.wish_id == wish.id)
        )
        for days_ago, status, rubles, is_minimum in observations:
            price = (
                WbPriceSchema(basic=rubles * 100, product=rubles * 100)
                if rubles is not None
                else None
            )
            record_fresh_observation(
                db,
                wish,
                ProductObservation(status=status, price=price, is_minimum=is_minimum),
                now - timedelta(days=days_ago),
            )
    db.commit()


# Рекомендации стенда (0015): три категории, одна — с картинкой-заглушкой, чтобы
# e2e видел и порядок под пол rich (male → hobby первой), и товар без картинки.
_SEED_RECOMMENDATIONS = (
    (
        RecommendationCategory.hobby,
        'Настольная игра Alias original',
        'https://www.wildberries.ru/catalog/173825315/detail.aspx',
        739,
    ),
    (
        RecommendationCategory.jewelry,
        'Серьги пусеты серебро 925',
        'https://www.wildberries.ru/catalog/149285080/detail.aspx',
        3811,
    ),
    (
        RecommendationCategory.beauty,
        'Крем для лица ночной 50 мл',
        'https://www.wildberries.ru/catalog/154859675/detail.aspx',
        None,
    ),
)


def _ensure_recommendations(db: Session) -> None:
    """Рекомендации по категориям для стенда, идемпотентно по ссылке."""
    existing = set(db.scalars(select(WishRecommendation.link)).all())
    for category, title, link, price in _SEED_RECOMMENDATIONS:
        if link not in existing:
            db.add(
                WishRecommendation(
                    title=title, link=link, price=price, category=category
                )
            )
    db.commit()
