from datetime import date
from unittest.mock import MagicMock

from app.constants import Gender
from app.db import User
from app.firebase import (
    create_custom_firebase_token,
    create_firebase_user,
    delete_firebase_user,
    get_firebase_user_data,
    send_push,
)
from app.vk import (
    exchange_tokens,
    get_extra_user_data_by_silent_token,
    get_gender,
    get_vk_user_data_by_access_token,
    get_vk_user_friends,
)


def test_get_gender():
    assert get_gender(1) == Gender.female
    assert get_gender(2) == Gender.male
    # 0 (не указан) и неизвестные коды → None, а не исключение.
    assert get_gender(0) is None
    assert get_gender(3) is None


def test_get_vk_user_data_by_access_token(mocker):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        'response': [
            {
                'id': 123,
                'first_name': 'Test',
                'last_name': 'User',
                'photo_200': 'http://photo.com',
                'sex': 1,
                'bdate': '01.01.2000',
            }
        ]
    }
    mocker.patch('httpx.get', return_value=mock_response)

    data = get_vk_user_data_by_access_token('token')

    assert data.id == 123
    assert data.first_name == 'Test'
    assert data.gender == Gender.female
    assert data.birthdate == date(2000, 1, 1)


def test_get_vk_user_friends(mocker):
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        'response': {'items': [{'id': 1, 'first_name': 'Friend'}]}
    }
    mocker.patch('httpx.get', return_value=mock_response)

    friends = get_vk_user_friends('token')
    assert len(friends) == 1
    assert friends[0]['id'] == 1


def test_firebase_send_push(mocker):
    mock_send = mocker.patch('firebase_admin.messaging.send_each')
    # send_each возвращает BatchResponse с одним успешным ответом
    ok_response = mocker.Mock(success=True, exception=None)
    mock_send.return_value = mocker.Mock(
        responses=[ok_response], success_count=1, failure_count=0
    )
    user = User(firebase_push_token='token1', id='uuid1')

    send_push([user], 'Title', 'Body')

    mock_send.assert_called_once()
    messages = mock_send.call_args[0][0]
    assert len(messages) == 1
    assert messages[0].token == 'token1'


def test_firebase_create_user(mocker):
    mock_create = mocker.patch('firebase_admin.auth.create_user')
    mock_user_record = MagicMock()
    mock_user_record.uid = 'new_uid'
    mock_create.return_value = mock_user_record

    uid = create_firebase_user('Name', 'http://photo', 'email@test.com', '123')
    assert uid == 'new_uid'
    mock_create.assert_called_once_with(
        email='email@test.com',
        email_verified=False,
        display_name='Name',
        photo_url='http://photo',
    )


def test_firebase_delete_user(mocker):
    mock_delete = mocker.patch('firebase_admin.auth.delete_user')
    delete_firebase_user('uid1')
    mock_delete.assert_called_once_with('uid1')


def test_firebase_create_custom_token(mocker):
    mock_token = mocker.patch('firebase_admin.auth.create_custom_token')
    mock_token.return_value = b'token_bytes'

    token = create_custom_firebase_token('uid1')
    assert token == 'token_bytes'
    mock_token.assert_called_once_with('uid1')


def test_firebase_get_user_data(mocker):
    mock_get = mocker.patch('firebase_admin.auth.get_user')
    mock_user = MagicMock()
    mock_user.uid = 'test_uid'
    mock_user.email = 'test@test.com'
    mock_user.display_name = 'Test User'
    mock_get.return_value = mock_user

    result = get_firebase_user_data('uid1')

    mock_get.assert_called_once_with('uid1')
    assert result.uid == 'test_uid'
    assert result.email == 'test@test.com'


def test_exchange_tokens(mocker):
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        'response': {
            'access_token': 'new_access_token',
            'phone': '12345',
            'email': 'test@test.com',
        }
    }
    mocker.patch('httpx.post', return_value=mock_response)

    token, extra = exchange_tokens('silent_token', 'uuid')
    assert token == 'new_access_token'
    assert extra.phone == '12345'
    assert extra.email == 'test@test.com'


def test_get_extra_user_data_by_silent_token(mocker):
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        'response': {'success': [{'phone': '12345', 'email': 'test@test.com'}]}
    }
    mocker.patch('httpx.post', return_value=mock_response)

    data = get_extra_user_data_by_silent_token('silent_token', 'uuid')
    assert data.phone == '12345'
    assert data.email == 'test@test.com'
