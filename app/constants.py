import enum


class Gender(enum.Enum):
    male = 'male'
    female = 'female'


class FollowAction(enum.Enum):
    """Тип события в логе подписок (append-only)."""

    follow = 'follow'
    unfollow = 'unfollow'


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
