from datetime import date

import pytest
from fastapi import HTTPException

from app.constants import Gender
from app.vk import (
    exchange_tokens,
    get_gender,
    get_vk_user_data_by_access_token,
    get_vk_user_friends,
)


def test_get_gender():
    assert get_gender(1) == Gender.female
    assert get_gender(2) == Gender.male
    with pytest.raises(KeyError):
        get_gender(3)


def test_get_vk_user_data_by_access_token(mocker):
    mock_response = mocker.Mock()
    mock_response.json.return_value = {
        'response': [
            {
                'id': 123,
                'first_name': 'Ivan',
                'last_name': 'Ivanov',
                'photo_200': 'http://photo',
                'sex': 2,
                'bdate': '01.01.1990',
            }
        ]
    }
    mocker.patch('httpx.get', return_value=mock_response)

    data = get_vk_user_data_by_access_token('token')
    assert data.id == 123
    assert data.gender == Gender.male
    assert data.birthdate == date(1990, 1, 1)


def test_get_vk_user_friends(mocker):
    mock_response = mocker.Mock()
    mock_response.json.return_value = {'response': {'items': [{'id': 1}, {'id': 2}]}}
    mocker.patch('httpx.get', return_value=mock_response)

    friends = get_vk_user_friends('token')
    assert len(friends) == 2
    assert friends[0]['id'] == 1


def test_exchange_tokens_success(mocker):
    mock_response = mocker.Mock()
    mock_response.json.return_value = {
        'response': {'access_token': 'new_token', 'email': 'test@test.com'}
    }
    mocker.patch('httpx.post', return_value=mock_response)

    token, extra = exchange_tokens('silent', 'uuid')
    assert token == 'new_token'
    assert extra.email == 'test@test.com'


def test_exchange_tokens_error(mocker):
    mock_response = mocker.Mock()
    mock_response.json.return_value = {'error': 'some error'}
    mocker.patch('httpx.post', return_value=mock_response)

    with pytest.raises(HTTPException) as exc:
        exchange_tokens('silent', 'uuid')
    assert exc.value.status_code == 401


def test_get_extra_user_data_by_silent_token_success(mocker):
    from app.vk import get_extra_user_data_by_silent_token

    mock_response = mocker.Mock()
    mock_response.json.return_value = {
        'response': {'success': [{'email': 'test@test.com'}]}
    }
    mocker.patch('httpx.post', return_value=mock_response)

    data = get_extra_user_data_by_silent_token('silent', 'uuid')
    assert data == {'email': 'test@test.com'}


def test_get_extra_user_data_by_silent_token_error(mocker):
    from app.vk import get_extra_user_data_by_silent_token

    mock_response = mocker.Mock()
    mock_response.json.return_value = {'response': {'errors': ['some error']}}
    mocker.patch('httpx.post', return_value=mock_response)

    with pytest.raises(HTTPException) as exc:
        get_extra_user_data_by_silent_token('silent', 'uuid')
    assert exc.value.status_code == 401
