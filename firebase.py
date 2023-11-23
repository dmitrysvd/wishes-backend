import firebase_admin
from firebase_admin.auth import verify_id_token

from config import settings

cred = firebase_admin.credentials.Certificate(settings.FIREBASE_KEY_PATH)
app = firebase_admin.initialize_app(cred)
id_token = ''
decoded_token = verify_id_token(id_token, app=app)
uid = decoded_token['uid']
