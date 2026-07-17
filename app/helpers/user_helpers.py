import os
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import User
from app.firebase import send_push
from app.logging import logger
from app.schemas import AnnotatedOtherUserSchema

# Каталог с загруженными аватарками на диске; отдаётся наружу через /media.
PROFILE_IMAGES_DIR = settings.MEDIA_ROOT / 'profile_images'


def save_profile_image_bytes(user: User, content: bytes, *, is_custom: bool) -> None:
    """Сохранить байты аватарки на диск и проставить пользователю ссылку.

    Единая точка записи фото на диск — используется и ручной загрузкой
    (`set_profile_image`, `is_custom=True`), и бэкфиллом соц-аватарок на диск
    (`is_custom=False`). URL строим из доверенного `FRONTEND_URL`, а не из Host
    запроса (host-header injection / stored URL poisoning).
    """
    PROFILE_IMAGES_DIR.mkdir(exist_ok=True, parents=True)
    file_name = f'profile_image_user_{user.id}_{datetime.now().isoformat()}'
    file_path = PROFILE_IMAGES_DIR / file_name
    file_path.write_bytes(content)
    related_media_path = file_path.relative_to(settings.MEDIA_ROOT)
    user.photo_url = f'{settings.FRONTEND_URL}/media/{related_media_path}'
    user.photo_path = str(file_path)
    user.photo_is_custom = is_custom


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
