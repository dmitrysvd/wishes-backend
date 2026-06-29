from datetime import date

from app.config import settings
from app.db import User

# Бренд-баннер — фолбэк og:image для владельцев без фото профиля.
BRAND_IMAGE_PATH = '/static/og_banner.png'
# Русские названия месяцев в родительном падеже («5 июля»).
_MONTHS_GENITIVE = (
    '',
    'января',
    'февраля',
    'марта',
    'апреля',
    'мая',
    'июня',
    'июля',
    'августа',
    'сентября',
    'октября',
    'ноября',
    'декабря',
)


def absolutize_url(url: str) -> str:
    """Делает ссылку абсолютной: краулеры не принимают относительные og:image/og:url."""
    if url.startswith(('http://', 'https://')):
        return url
    return f'{settings.FRONTEND_URL}{url}'


def pluralize_wishes(count: int) -> str:
    """«1 желание» / «2 желания» / «5 желаний» — по правилам русского склонения."""
    tail10 = count % 10
    tail100 = count % 100
    if tail10 == 1 and tail100 != 11:
        word = 'желание'
    elif 2 <= tail10 <= 4 and not 12 <= tail100 <= 14:
        word = 'желания'
    else:
        word = 'желаний'
    return f'{count} {word}'


def format_birthday(birth_date: date) -> str:
    """«5 июля» — день и месяц без года (год — PII, наружу не отдаём)."""
    return f'{birth_date.day} {_MONTHS_GENITIVE[birth_date.month]}'


def build_og_image_url(user: User) -> str:
    """og:image: фото профиля владельца (лицо = высокий CTR), иначе бренд-баннер."""
    if user.photo_url:
        return absolutize_url(user.photo_url)
    return absolutize_url(BRAND_IMAGE_PATH)


def build_og_description(user: User, wish_count: int) -> str:
    """Подзаголовок превью: число желаний и (если есть) дата ДР."""
    parts = [pluralize_wishes(wish_count) if wish_count else 'Список желаний']
    if user.birth_date:
        parts.append(f'ДР {format_birthday(user.birth_date)}')
    return ' · '.join(parts)


def build_og_context(user: User | None, wish_count: int) -> dict[str, str]:
    """Контекст для шаблона og_user.html.

    `user is None` — ссылка на удалённого/несуществующего владельца: отдаём
    нейтральное бренд-превью (а не битую карточку), чтобы ссылка всё равно
    работала рекламой приложения.
    """
    if user is None:
        return {
            'title': 'Хотелки',
            'description': 'Список желаний по ссылке — узнай, что подарить',
            'image': absolutize_url(BRAND_IMAGE_PATH),
            'url': settings.FRONTEND_URL,
        }
    return {
        'title': f'Хотелки · {user.display_name}',
        'description': build_og_description(user, wish_count),
        'image': build_og_image_url(user),
        'url': f'{settings.FRONTEND_URL}/user?userId={user.id}',
    }
