"""Группы уведомлений (фича 0012): маппинг типов пушей, тексты, чтение и переключение.

Единственное место, где тип пуша знает свою группу и где читается положение
переключателя. `send_push` фильтрует адресатов через `disabled_user_ids`, поэтому
ни один пуш не уходит в обход настроек.
"""

from collections.abc import Iterable
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import NotificationGroup
from app.db import NotificationSetting, NotificationSettingEvent, PushReason, User
from app.logging import logger


class GroupTexts(NamedTuple):
    title: str
    subtitle: str


# Тексты строк экрана. Лимиты — контракт: title ≤ 30, subtitle ≤ 45 символов
# (одна строка на 320px); проверяются тестом против схемы.
GROUP_TEXTS: dict[NotificationGroup, GroupTexts] = {
    NotificationGroup.reservation: GroupTexts(
        'Резерв', 'Кто-то зарезервировал твою хотелку'
    ),
    NotificationGroup.friends: GroupTexts(
        'Друзья', 'Новый подписчик, обновления списков подписок'
    ),
    NotificationGroup.birthdays: GroupTexts(
        'Дни рождения', 'Твой день рождения и дни рождения подписок'
    ),
    NotificationGroup.prices: GroupTexts(
        'Цены и наличие', 'Вещь из списка подешевела или снова в наличии'
    ),
    NotificationGroup.tips: GroupTexts(
        'Советы и подборки', 'Сезонные подборки и подсказки новичку'
    ),
}

# Каждый тип пуша — ровно в одной группе. Тип без группы — ошибка продукта:
# тест `test_every_push_reason_has_group` не даст добавить PushReason без строки.
PUSH_REASON_GROUP: dict[PushReason, NotificationGroup] = {
    PushReason.RESERVATION: NotificationGroup.reservation,
    PushReason.NEW_FOLLOWER: NotificationGroup.friends,
    PushReason.INVITE_JOINED: NotificationGroup.friends,
    PushReason.WISH_CREATION: NotificationGroup.friends,
    PushReason.CURRENT_USER_BIRTHDAY: NotificationGroup.birthdays,
    PushReason.FOLLOWER_BIRTHDAY: NotificationGroup.birthdays,
    PushReason.PRICE_ALERT: NotificationGroup.prices,
    PushReason.SEASONAL: NotificationGroup.tips,
    PushReason.EMPTY_LIST_REACTIVATION: NotificationGroup.tips,
}


def disabled_user_ids(
    db: Session, user_ids: Iterable[UUID], reason: PushReason
) -> set[UUID]:
    """Кто из `user_ids` выключил группу пуша `reason`. Нет строки = включено."""
    group = PUSH_REASON_GROUP[reason]
    return set(
        db.scalars(
            select(NotificationSetting.user_id).where(
                NotificationSetting.user_id.in_(list(user_ids)),
                NotificationSetting.group == group,
                NotificationSetting.enabled.is_(False),
            )
        )
    )


def group_states(db: Session, user: User) -> list[tuple[NotificationGroup, bool]]:
    """Все группы в порядке показа с положением переключателя (дефолт — включено)."""
    saved = {
        setting.group: setting.enabled
        for setting in db.scalars(
            select(NotificationSetting).where(NotificationSetting.user_id == user.id)
        )
    }
    return [(group, saved.get(group, True)) for group in NotificationGroup]


def set_group_enabled(
    db: Session, user: User, group: NotificationGroup, enabled: bool
) -> bool:
    """Переключить группу; событие пишется только при реальной смене положения.
    Возвращает, изменилось ли положение.

    Абсолютное значение и одна строка на (юзер, группа) — повтор запроса и гонка
    устройств сводятся к «последняя запись побеждает» без версий. Коммит — здесь:
    настройка и событие должны попасть в БД вместе.
    """
    setting = db.scalars(
        select(NotificationSetting).where(
            NotificationSetting.user_id == user.id,
            NotificationSetting.group == group,
        )
    ).one_or_none()
    was_enabled = setting.enabled if setting else True
    if setting is None:
        setting = NotificationSetting(user_id=user.id, group=group, enabled=enabled)
        db.add(setting)
    else:
        setting.enabled = enabled
    if was_enabled != enabled:
        db.add(NotificationSettingEvent(user_id=user.id, group=group, enabled=enabled))
        logger.info(
            'Группа уведомлений {group} {state}: user={user_id}',
            group=group.value,
            state='включена' if enabled else 'выключена',
            user_id=user.id,
        )
    db.commit()
    return was_enabled != enabled
