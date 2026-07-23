from datetime import date

from fastapi import APIRouter, Depends
from pydantic import HttpUrl
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.status import HTTP_401_UNAUTHORIZED

from app.constants import BirthdayRadarKind
from app.db import User
from app.dependencies import USERS_TAG, get_current_user, get_db
from app.schemas import (
    BirthdayRadarEntrySchema,
    BirthdayRadarSchema,
    PublicBirthdaySchema,
)

router = APIRouter(tags=[USERS_TAG])


def parse_vk_bdate_day_month(bdate: str | None) -> tuple[int, int] | None:
    """Разобрать VK `bdate` до (день, месяц). Год не нужен и отбрасывается.

    VK отдаёт `D.M.YYYY` (полная дата) или `D.M` (год скрыт — таких много). Радару
    нужен только день+месяц, поэтому, в отличие от `app.vk._parse_vk_birthdate`
    (который бросает даты без года), здесь берём и `D.M`. Битое/пустое → None.
    Валидируем через високосный 2000 год, чтобы пропустить 29 февраля.
    """
    if not bdate:
        return None
    parts = bdate.split('.')
    if len(parts) < 2:
        return None
    try:
        day = int(parts[0])
        month = int(parts[1])
        date(2000, month, day)  # 2000 високосный — допускает 29.02, ловит мусор
    except (ValueError, TypeError):
        return None
    return day, month


def _next_birthday_occurrence(day: int, month: int, today: date) -> date:
    """Ближайшая дата (сегодня или в будущем) для дня рождения (день, месяц).

    Устойчиво к 29 февраля: в невисокосный год отмечаем 28 февраля.
    """

    def occurrence_in(year: int) -> date:
        try:
            return date(year, month, day)
        except ValueError:
            return date(year, month, 28)

    result = occurrence_in(today.year)
    if result < today:
        result = occurrence_in(today.year + 1)
    return result


def days_until_birthday(day: int, month: int, today: date) -> int:
    return (_next_birthday_occurrence(day, month, today) - today).days


def build_birthday_radar(
    db: Session, current_user: User, today: date
) -> BirthdayRadarSchema:
    """Собрать радар: приближающиеся ДР VK-друзей ∪ подписок текущего юзера.

    `today` параметризован ради тестируемости без подмены системного времени.
    """
    vk_linked = current_user.vk_id is not None

    # 1. VK-друзья с распознанным днём рождения: vk_id -> (имя, день, месяц).
    vk_friends: dict[str, tuple[str, int, int]] = {}
    for friend in current_user.vk_friends_data or []:
        friend_id = friend.get('id')
        day_month = parse_vk_bdate_day_month(friend.get('bdate'))
        if friend_id is None or day_month is None:
            continue
        name = f'{friend.get("first_name", "")} {friend.get("last_name", "")}'.strip()
        vk_friends[str(friend_id)] = (name or 'Друг из ВК', day_month[0], day_month[1])

    # 2. Кто из VK-друзей уже в приложении (матч по vk_id, как в possible_friends).
    app_users_by_vk: dict[str, User] = {}
    if vk_friends:
        found = db.scalars(select(User).where(User.vk_id.in_(vk_friends.keys()))).all()
        app_users_by_vk = {user.vk_id: user for user in found if user.vk_id}

    followed_ids = {followed.id for followed in current_user.follows}
    entries: list[BirthdayRadarEntrySchema] = []
    seen_user_ids: set = set()

    def add_in_app(user: User, fallback_day_month: tuple[int, int] | None) -> None:
        # Один человек в радаре один раз; себя не показываем.
        if user.id in seen_user_ids or user.id == current_user.id:
            return
        # День рождения: из профиля, иначе из снимка VK. Нет ни там ни там — пропуск.
        if user.birth_date is not None:
            day, month = user.birth_date.day, user.birth_date.month
        elif fallback_day_month is not None:
            day, month = fallback_day_month
        else:
            return
        seen_user_ids.add(user.id)
        entries.append(
            BirthdayRadarEntrySchema(
                kind=BirthdayRadarKind.in_app,
                display_name=user.display_name,
                # В БД photo_url — строка; контракт отдаёт HttpUrl, коэрсим явно.
                photo_url=HttpUrl(user.photo_url) if user.photo_url else None,
                birthday=PublicBirthdaySchema(day=day, month=month),
                days_until_birthday=days_until_birthday(day, month, today),
                user_id=user.id,
                active_wishes_count=sum(
                    1 for wish in user.wishes if not wish.is_archived
                ),
                followed_by_me=user.id in followed_ids,
                vk_id=None,
            )
        )

    # 3а. VK-друзья, которые есть в приложении → in_app (ДР из профиля, фолбэк — VK).
    for vk_id, (_, day, month) in vk_friends.items():
        app_user = app_users_by_vk.get(vk_id)
        if app_user is not None:
            add_in_app(app_user, fallback_day_month=(day, month))

    # 3б. Подписки с известным ДР → in_app (ДР только из профиля).
    for followed in current_user.follows:
        add_in_app(followed, fallback_day_month=None)

    # 4. VK-друзья без аккаунта → invite (крючок роста графа).
    for vk_id, (name, day, month) in vk_friends.items():
        if vk_id in app_users_by_vk:
            continue
        entries.append(
            BirthdayRadarEntrySchema(
                kind=BirthdayRadarKind.invite,
                display_name=name,
                photo_url=None,
                birthday=PublicBirthdaySchema(day=day, month=month),
                days_until_birthday=days_until_birthday(day, month, today),
                user_id=None,
                active_wishes_count=None,
                followed_by_me=None,
                vk_id=vk_id,
            )
        )

    # 5. Сортировка по близости ДР, затем по имени (стабильный порядок).
    entries.sort(key=lambda entry: (entry.days_until_birthday, entry.display_name))
    return BirthdayRadarSchema(vk_linked=vk_linked, entries=entries)


@router.get(
    '/birthday_radar',
    response_model=BirthdayRadarSchema,
    responses={
        200: {
            'description': (
                'Радар собран. `entries` отсортирован по близости ДР (ближайшие '
                'сверху) и отдаётся целиком. Пустой `entries` — нет известных ДР; '
                'какое пустое состояние показать, различайте по `vk_linked`.'
            )
        },
        HTTP_401_UNAUTHORIZED: {
            'description': (
                'Нет или истёк токен авторизации. Радар доступен только '
                'авторизованному юзеру.'
            )
        },
    },
)
def birthday_radar(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BirthdayRadarSchema:
    """Приближающиеся дни рождения друзей — повод вернуться и подарить (фича 0007).

    Собирает ДР VK-друзей юзера и тех, на кого он подписан в приложении (с указанной
    датой), схлопывает дубли и сортирует по близости. Для человека с аккаунтом
    (`kind = in_app`) даёт навигацию в его список; для VK-друга без аккаунта
    (`kind = invite`) — повод пригласить. Приватность: год рождения не отдаётся.
    """
    return build_birthday_radar(db, user, date.today())
