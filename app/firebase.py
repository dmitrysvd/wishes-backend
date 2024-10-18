from typing import Optional, cast

import firebase_admin
from firebase_admin import App, auth, messaging
from firebase_admin.auth import UserRecord

from app.config import settings
from app.db import User
from app.logging import logger

cred = firebase_admin.credentials.Certificate(settings.FIREBASE_KEY_PATH)

firebase_admin.initialize_app(cred)


def send_push(target_users: list[User], title: str, body: str, link: str | None = None):
    if not target_users:
        logger.info('Пустой список получателей. Пуши не отправлены.')
        return
    data = {
        'click_action': 'FLUTTER_NOTIFICATION_CLICK',
    }
    if link:
        data['link'] = link
    android_notification = messaging.AndroidNotification(
        title=title,
        body=body,
    )
    android_config = messaging.AndroidConfig(notification=android_notification)
    messages = []
    users_with_message_ids = []
    for user in target_users:
        if not user.firebase_push_token:
            logger.warning(
                'Не отправлено сообщение из-за отсутствия пуш-токена: {user_id}',
                user_id=user.id,
            )
            continue
        message = messaging.Message(
            android=android_config,
            token=user.firebase_push_token,
            data=data,
        )
        messages.append(message)
        users_with_message_ids.append(user.id)
    logger.info(
        f'Отправка {len(messages)} собщений пользователям: {users_with_message_ids}'
    )
    messaging.send_each(messages, dry_run=settings.IS_DEBUG)


def create_firebase_user(
    display_name: str,
    photo_url: str,
    email: str | None,
    phone: str | None,
) -> str:
    user: UserRecord = auth.create_user(
        email=email,
        email_verified=False,
        display_name=display_name,
        photo_url=photo_url,
    )
    return user.uid  # type: ignore


def delete_firebase_user(uid: str) -> None:
    auth.delete_user(uid)


def create_custom_firebase_token(uid: str) -> str:
    custom_token = auth.create_custom_token(uid)
    return custom_token.decode()


def get_firebase_user_data(uid: str) -> UserRecord:
    return auth.get_user(uid)
