from datetime import datetime, timezone

from app.alerts import send_tg_channel_message
from app.db import User
from app.logging import logger


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_user_handler(user: User):
    logger.info(
        'Зарегистрирован новый пользователь: firebase_uid={firebase_uid}',
        firebase_uid=user.firebase_uid,
    )

    msg = f'Зарегистрирован новый пользователь {user.display_name} https://hotelki.pro/user?userId={user.id}'
    try:
        send_tg_channel_message(msg)
    except Exception as ex:
        pass
