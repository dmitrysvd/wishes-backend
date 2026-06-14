import enum


class Gender(enum.Enum):
    male = 'male'
    female = 'female'


# Пагинация
DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100
