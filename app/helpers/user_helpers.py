import os
from typing import Union

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import User
from app.firebase import send_push
from app.logging import logger
from app.schemas import AnnotatedOtherUserSchema


def get_annotated_users(
    db: Session,
    current_user: User,
    outer_users: Union[Select[tuple[User]], list[User], None] = None,
) -> list[AnnotatedOtherUserSchema]:
    query = select(
        User,
        User.followed_by.any(User.id == current_user.id).label('followed_by_me'),
        User.follows.any(User.id == current_user.id).label('follows_me'),
    )
    if isinstance(outer_users, Select):
        user_ids = [user.id for user in db.execute(outer_users).scalars()]
        query = query.where(User.id.in_(user_ids))
    elif isinstance(outer_users, list):
        user_ids = [user.id for user in outer_users]
        query = query.where(User.id.in_(user_ids))
    values = db.execute(query).all()
    for user, followed_by_me, follows_me in values:
        user.followed_by_me = followed_by_me  # type: ignore
        user.follows_me = follows_me  # type: ignore
    return [AnnotatedOtherUserSchema.model_validate(val[0]) for val in values]


def get_user_deep_link(user: User) -> str:
    return f'{settings.FRONTEND_URL}/user?userId={user.id}#'


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
    if photo_path_to_delete and os.path.exists(photo_path_to_delete):
        os.remove(photo_path_to_delete)
    db.add(user)
    db.commit()
