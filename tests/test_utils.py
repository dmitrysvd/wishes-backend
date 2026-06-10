from app.db import User
from app.utils import new_user_handler


def test_new_user_handler_error(mocker):
    # Coverage for the try-except block in new_user_handler
    mocker.patch('app.utils.send_tg_channel_message', side_effect=Exception('TG error'))
    user = User(id='uuid', display_name='Test', firebase_uid='fb_uid')
    # Should not raise
    new_user_handler(user)
