from typing import Optional, cast

import firebase_admin
from firebase_admin import App, auth, messaging
from firebase_admin.auth import UserRecord

from app.config import settings

cred = firebase_admin.credentials.Certificate(settings.FIREBASE_KEY_PATH)

_firebase_app: Optional[App] = firebase_admin.initialize_app(cred)


def get_firebase_app():
    return _firebase_app


def send_push(push_tokens: list[str], title: str, body: str, link: str | None = None):
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
    for push_token in push_tokens:
        message = messaging.Message(
            android=android_config,
            token=push_token,
            data=data,
        )
        messages.append(message)
    messaging.send_all(messages)


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
