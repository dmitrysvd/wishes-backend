import firebase_admin

from config import settings

cred = firebase_admin.credentials.Certificate(settings.FIREBASE_KEY_PATH)

firebase_app = firebase_admin.initialize_app(cred)
