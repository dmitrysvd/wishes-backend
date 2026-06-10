from unittest.mock import MagicMock

from app.db import User
from app.firebase import (
    create_custom_firebase_token,
    create_firebase_user,
    delete_firebase_user,
    get_firebase_user_data,
    send_push,
)


def test_send_push_no_users(mocker):
    mock_logger = mocker.patch('app.firebase.logger')
    send_push([], 'title', 'body')
    mock_logger.info.assert_any_call('Пустой список получателей. Пуши не отправлены.')


def test_send_push_with_users(mocker):
    mock_messaging = mocker.patch('app.firebase.messaging')
    user = User(id=1, firebase_push_token='token')

    send_push([user], 'title', 'body', link='http://link')

    mock_messaging.Message.assert_called_once()
    mock_messaging.send_each.assert_called_once()


def test_send_push_no_token(mocker):
    mock_logger = mocker.patch('app.firebase.logger')
    user = User(id=1, firebase_push_token=None)

    send_push([user], 'title', 'body')
    mock_logger.warning.assert_called()


def test_create_firebase_user(mocker):
    mock_auth = mocker.patch('app.firebase.auth')
    mock_user = MagicMock()
    mock_user.uid = 'test_uid'
    mock_auth.create_user.return_value = mock_user

    uid = create_firebase_user('name', 'photo', 'email', 'phone')
    assert uid == 'test_uid'
    mock_auth.create_user.assert_called_once_with(
        email='email', email_verified=False, display_name='name', photo_url='photo'
    )


def test_delete_firebase_user(mocker):
    mock_auth = mocker.patch('app.firebase.auth')
    delete_firebase_user('uid')
    mock_auth.delete_user.assert_called_once_with('uid')


def test_create_custom_firebase_token(mocker):
    mock_auth = mocker.patch('app.firebase.auth')
    mock_auth.create_custom_token.return_value = b'token'
    token = create_custom_firebase_token('uid')
    assert token == 'token'


def test_get_firebase_user_data(mocker):
    mock_auth = mocker.patch('app.firebase.auth')
    get_firebase_user_data('uid')
    mock_auth.get_user.assert_called_once_with('uid')
