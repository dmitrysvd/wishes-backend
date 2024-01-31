from typing import Optional, cast

import firebase_admin
from firebase_admin import App, auth, messaging
from firebase_admin.auth import UserRecord

from app.config import settings

cred = firebase_admin.credentials.Certificate(settings.FIREBASE_KEY_PATH)

_firebase_app: Optional[App] = firebase_admin.initialize_app(cred)


def get_firebase_app():
    return _firebase_app


def send_push(push_token: str, title: str, body: str):
    android_notification = messaging.AndroidNotification(
        title=title,
        body=body,
    )
    android_config = messaging.AndroidConfig(notification=android_notification)
    message = messaging.Message(
        android=android_config,
        token=push_token,
    )
    messaging.send(message)


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


def create_custom_firebase_token(uid: str) -> str:
    custom_token = auth.create_custom_token(uid)
    return custom_token.decode()


def get_firebase_user_data(uid: str) -> UserRecord:
    return auth.get_user(uid)
