import enum


class Gender(enum.Enum):
    male = 'male'
    female = 'female'


class FollowAction(enum.Enum):
    """Тип события в логе подписок (append-only)."""

    follow = 'follow'
    unfollow = 'unfollow'


class BirthdayRadarKind(enum.Enum):
    """Тип строки в бёрздей-радаре — как с ней взаимодействовать.

    in_app — у человека есть аккаунт в приложении: тап ведёт в его список (S5),
             можно подписаться/зарезервировать.
    invite — человек только среди VK-друзей, аккаунта в приложении нет: показываем
             CTA «Пригласить» (шеринг инвайт-ссылки текущего юзера, петля 0003).
    """

    in_app = 'in_app'
    invite = 'invite'


class FollowSource(enum.Enum):
    """Экран-источник, с которого пришли на профиль перед подпиской.

    Проставляется клиентом; nullable в логе (старые клиенты не шлют).
    """

    search = 'search'  # результаты текстового поиска людей
    possible_friends = 'possible_friends'  # блок «возможные друзья» (VK-сидинг)
    followers_list = 'followers_list'  # экран подписчиков/подписок
    deeplink = 'deeplink'  # профиль открыт по расшаренной ссылке
    other = 'other'  # прочее/неизвестно


# Пагинация
DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100

# Реферальная атрибуция: внутренний лимит длины канала установки (utm_source).
# На проводе длина не ограничена — переразмерное значение молча усекается до этого
# лимита (best-effort, без 422). См. фичу 0003 referral-attribution.
UTM_SOURCE_MAX_LENGTH = 64
