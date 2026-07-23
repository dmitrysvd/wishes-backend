"""dev/test-байпас аутентификации (фича 0009).

Материализует детерминированных сид-юзеров и собирает bearer, который принимает
`get_current_user`. Токен выдаётся/принимается ТОЛЬКО для сид-юзеров (`is_test`),
поэтому байпас безопасен даже в проде-подобной среде: утёкший секрет не даёт
войти в реальный аккаунт. Включённость гейтится наличием `TEST_AUTH_SECRET`.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import Gender, TestPersona
from app.db import User, Wish
from app.utils import utc_now

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
        return _get_or_create_rich(db)
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

    db.commit()
    return user
