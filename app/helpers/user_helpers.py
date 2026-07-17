import os
import re
from collections.abc import Sequence
from datetime import datetime

import httpx
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import User
from app.firebase import send_push
from app.logging import logger
from app.schemas import AnnotatedOtherUserSchema

# Каталог с загруженными аватарками на диске; отдаётся наружу через /media.
PROFILE_IMAGES_DIR = settings.MEDIA_ROOT / 'profile_images'
# Таймаут на скачивание одной аватарки, секунды.
AVATAR_DOWNLOAD_TIMEOUT_SECONDS = 15
# Целевой размер стороны для Google-аватарок (см. upscale_google_avatar_url).
TARGET_AVATAR_SIZE = 512


def guess_image_extension(content: bytes) -> str:
    """Расширение картинки по сигнатуре (magic bytes), с ведущей точкой.

    Нужно, чтобы файлы на диске имели расширение и StaticFiles/nginx отдавали их
    с корректным Content-Type (иначе — application/octet-stream). Пустая строка,
    если тип не распознан (сохраним без расширения, как раньше).
    """
    if content.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if content.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if content.startswith((b'GIF87a', b'GIF89a')):
        return '.gif'
    if content[:4] == b'RIFF' and content[8:12] == b'WEBP':
        return '.webp'
    return ''


def save_profile_image_bytes(user: User, content: bytes, *, is_custom: bool) -> None:
    """Сохранить байты аватарки на диск и проставить пользователю ссылку.

    Единая точка записи фото на диск — используется и ручной загрузкой
    (`set_profile_image`, `is_custom=True`), и бэкфиллом соц-аватарок на диск
    (`is_custom=False`). URL строим из доверенного `FRONTEND_URL`, а не из Host
    запроса (host-header injection / stored URL poisoning). Файл сохраняем с
    расширением по типу картинки — чтобы отдавался с корректным Content-Type.
    """
    PROFILE_IMAGES_DIR.mkdir(exist_ok=True, parents=True)
    extension = guess_image_extension(content)
    file_name = f'profile_image_user_{user.id}_{datetime.now().isoformat()}{extension}'
    file_path = PROFILE_IMAGES_DIR / file_name
    file_path.write_bytes(content)
    related_media_path = file_path.relative_to(settings.MEDIA_ROOT)
    user.photo_url = f'{settings.FRONTEND_URL}/media/{related_media_path}'
    user.photo_path = str(file_path)
    user.photo_is_custom = is_custom


def upscale_google_avatar_url(url: str) -> str:
    """Поднять разрешение Google-аватарки до TARGET_AVATAR_SIZE px.

    Firebase отдаёт Google-фото с зашитым в URL размером `=s96-c` (96px), из-за
    чего аватарки пикселят при показе крупнее. Google по тому же URL отдаёт вплоть
    до оригинала, если заменить токен размера. Не-Google URL (VK и пр.) — как есть.
    """
    if 'googleusercontent.com' not in url:
        return url
    # Токен размера у Google-аватарок: `=sNN` или `=sNN-c` (обычно в конце URL).
    upscaled, replaced = re.subn(r'=s\d+(-c)?', f'=s{TARGET_AVATAR_SIZE}-c', url)
    if replaced:
        return upscaled
    return f'{url}=s{TARGET_AVATAR_SIZE}-c'


def download_avatar_bytes(url: str, client: httpx.Client | None = None) -> bytes | None:
    """Скачать аватарку (Google — в высоком разрешении). None при ошибке.

    Ошибку скачивания логируем и глушим (возвращаем None): вызывающий решает,
    что делать (обнулить битую ссылку / оставить текущее фото).
    """
    own_client = client is None
    if client is None:
        client = httpx.Client(timeout=AVATAR_DOWNLOAD_TIMEOUT_SECONDS)
    try:
        response = client.get(upscale_google_avatar_url(url), follow_redirects=True)
        response.raise_for_status()
        return response.content
    except httpx.HTTPError as exc:
        logger.warning('Не удалось скачать аватарку url={url}: {exc}', url=url, exc=exc)
        return None
    finally:
        if own_client:
            client.close()


def refresh_avatar_on_login(
    user: User,
    social_photo_url: str | None,
    db: Session,
    client: httpx.Client | None = None,
) -> None:
    """Обновить аватарку пользователя из соц-сети при логине (best-effort).

    Кастомное фото (`photo_is_custom`) не трогаем. Если соц-фото нет или скачать
    не удалось — оставляем текущее (для нового юзера это «нет фото» → инициалы).
    При успехе перекачиваем свежую соц-аватарку на диск, отражая смену аватара
    в соц-сети. Сбой не должен ронять логин — вызывать в конце, после коммита.
    """
    if user.photo_is_custom or not social_photo_url:
        return
    content = download_avatar_bytes(social_photo_url, client)
    if content is None:
        return
    save_profile_image_bytes(user, content, is_custom=False)
    db.add(user)
    db.commit()


def get_annotated_users(
    db: Session,
    current_user: User,
    outer_users: Select[tuple[User]] | Sequence[User] | None = None,
) -> list[AnnotatedOtherUserSchema]:
    query = select(
        User,
        User.followed_by.any(User.id == current_user.id).label('followed_by_me'),
        User.follows.any(User.id == current_user.id).label('follows_me'),
    )
    if isinstance(outer_users, Select):
        user_ids = [user.id for user in db.execute(outer_users).scalars()]
        query = query.where(User.id.in_(user_ids))
    elif outer_users is not None:
        user_ids = [user.id for user in outer_users]
        query = query.where(User.id.in_(user_ids))
    values = db.execute(query).all()
    for user, followed_by_me, follows_me in values:
        user.followed_by_me = followed_by_me
        user.follows_me = follows_me
    return [AnnotatedOtherUserSchema.model_validate(val[0]) for val in values]


def get_user_deep_link(user: User, ref: User | None = None) -> str:
    """Deep link на страницу списка пользователя.

    Если передан `ref` (пригласивший) — ссылка несёт реф-метку `ref={ref.id}`,
    основу реферальной атрибуции (фича 0003). Без `ref` — обычный deep link
    (пуши, шеринг чужого списка), метку не добавляем.
    """
    link = f'{settings.FRONTEND_URL}/user?userId={user.id}'
    if ref is not None:
        link += f'&ref={ref.id}'
    return f'{link}#'


def send_push_about_new_follower(target: User, follower: User):
    if not target.firebase_push_token:
        return
    send_push(
        target_users=[target],
        title='У вас новый подписчик',
        body=f'На вас подписался {follower.display_name}',
        link=get_user_deep_link(follower),
    )
    logger.info(f'Отправлен пуш при подписании {follower.id} на {target.id}')


def delete_user_image(user: User, db: Session):
    """Удалить фото профиля пользователя.

    ИСПРАВЛЕН БАГ: Теперь сохраняем путь к файлу перед обнулением,
    чтобы проверка os.path.exists работала корректно.
    """
    photo_path_to_delete = user.photo_path
    user.photo_path = None
    user.photo_url = None
    user.photo_is_custom = False
    if photo_path_to_delete and os.path.exists(photo_path_to_delete):
        os.remove(photo_path_to_delete)
    db.add(user)
    db.commit()
