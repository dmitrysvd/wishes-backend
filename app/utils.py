from datetime import datetime, timezone

from app.db import User
from app.logging import logger


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_user_handler(user: User):
    logger.info(
        'Зарегистрирован новый пользователь: firebase_uid={firebase_uid}',
        firebase_uid=user.firebase_uid,
    )
