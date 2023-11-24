from typing import Optional

import firebase_admin
from firebase_admin import App, messaging

from config import settings

cred = firebase_admin.credentials.Certificate(settings.FIREBASE_KEY_PATH)

_firebase_app: Optional[App] = firebase_admin.initialize_app(cred)


def get_firebase_app():
    return _firebase_app


def send_push(push_token: str, title: str, body: str):
    message = messaging.Message(
        notification=messaging.Notification(
            title=title,
            body=body,
        ),
        token=push_token,
    )
    messaging.send(message)
