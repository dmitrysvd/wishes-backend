import enum


class Gender(enum.Enum):
    male = 'male'
    female = 'female'


# Пагинация
DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100

# Реферальная атрибуция: внутренний лимит длины канала установки (utm_source).
# На проводе длина не ограничена — переразмерное значение молча усекается до этого
# лимита (best-effort, без 422). См. фичу 0003 referral-attribution.
UTM_SOURCE_MAX_LENGTH = 64
