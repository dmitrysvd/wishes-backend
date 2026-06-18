from app.db import User
from app.utils import new_user_handler


def test_new_user_handler(mocker):
    # Логирует регистрацию и не падает
    mock_logger = mocker.patch('app.utils.logger')
    user = User(id='uuid', display_name='Test', firebase_uid='fb_uid')
    new_user_handler(user)
    mock_logger.info.assert_called_once()
