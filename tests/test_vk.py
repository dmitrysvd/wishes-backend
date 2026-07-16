from datetime import date

import pytest
from fastapi import HTTPException

from app.constants import Gender
from app.vk import (
    VkResponseError,
    exchange_tokens,
    exchange_vk_code,
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


def test_get_vk_user_data_by_access_token_hidden_fields(mocker):
    # Юзер скрыл пол и год рождения: sex отсутствует, bdate без года.
    mock_response = mocker.Mock()
    mock_response.json.return_value = {
        'response': [
            {
                'id': 123,
                'first_name': 'Ivan',
                'last_name': 'Ivanov',
                'photo_200': 'http://photo',
                'bdate': '1.5',
            }
        ]
    }
    mocker.patch('httpx.get', return_value=mock_response)

    data = get_vk_user_data_by_access_token('token')
    assert data.gender is None
    assert data.birthdate is None


def test_get_vk_user_data_by_access_token_unspecified_sex(mocker):
    # sex=0 (не указан) → gender None, регистрация не падает.
    mock_response = mocker.Mock()
    mock_response.json.return_value = {
        'response': [
            {
                'id': 123,
                'first_name': 'Ivan',
                'last_name': 'Ivanov',
                'photo_200': 'http://photo',
                'sex': 0,
            }
        ]
    }
    mocker.patch('httpx.get', return_value=mock_response)

    data = get_vk_user_data_by_access_token('token')
    assert data.gender is None
    assert data.birthdate is None


def test_get_vk_user_data_by_access_token_error(mocker):
    mock_response = mocker.Mock()
    mock_response.json.return_value = {'error': 'invalid token'}
    mocker.patch('httpx.get', return_value=mock_response)

    with pytest.raises(HTTPException) as exc:
        get_vk_user_data_by_access_token('token')
    assert exc.value.status_code == 401


def test_get_vk_user_data_by_access_token_malformed(mocker):
    # Битая структура без явной ошибки VK → сбой интеграции (5xx → Hawk), не 401.
    mock_response = mocker.Mock()
    mock_response.json.return_value = {'response': [{'id': 123}]}
    mocker.patch('httpx.get', return_value=mock_response)

    with pytest.raises(VkResponseError):
        get_vk_user_data_by_access_token('token')


def test_get_vk_user_data_by_access_token_empty(mocker):
    mock_response = mocker.Mock()
    mock_response.json.return_value = {'response': []}
    mocker.patch('httpx.get', return_value=mock_response)

    with pytest.raises(VkResponseError):
        get_vk_user_data_by_access_token('token')


def test_get_vk_user_friends(mocker):
    mock_response = mocker.Mock()
    mock_response.json.return_value = {'response': {'items': [{'id': 1}, {'id': 2}]}}
    mocker.patch('httpx.get', return_value=mock_response)

    friends = get_vk_user_friends('token')
    assert len(friends) == 2
    assert friends[0]['id'] == 1


def test_get_vk_user_friends_error(mocker):
    # VK явно вернул ошибку (протухший токен) → 401.
    mock_response = mocker.Mock()
    mock_response.json.return_value = {'error': 'token expired'}
    mocker.patch('httpx.get', return_value=mock_response)

    with pytest.raises(HTTPException) as exc:
        get_vk_user_friends('token')
    assert exc.value.status_code == 401


def test_get_vk_user_friends_malformed(mocker):
    mock_response = mocker.Mock()
    mock_response.json.return_value = {'response': {'items': [{'no_id': 1}]}}
    mocker.patch('httpx.get', return_value=mock_response)

    with pytest.raises(VkResponseError):
        get_vk_user_friends('token')


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


def test_exchange_tokens_malformed(mocker):
    mock_response = mocker.Mock()
    mock_response.json.return_value = {'response': {'no_access_token': True}}
    mocker.patch('httpx.post', return_value=mock_response)

    with pytest.raises(VkResponseError):
        exchange_tokens('silent', 'uuid')


def test_exchange_vk_code_success(mocker):
    # VK ID (OAuth 2.1) отдаёт плоский ответ; email/phone — подтверждённые VK.
    mock_response = mocker.Mock()
    mock_response.json.return_value = {
        'access_token': 'vk2.a.new_token',
        'refresh_token': 'vk2.r.refresh',
        'email': 'confirmed@test.com',
        'phone': '+70000000000',
        'user_id': 123,
    }
    mocker.patch('httpx.post', return_value=mock_response)

    token, extra = exchange_vk_code('code', 'verifier', 'device')
    assert token == 'vk2.a.new_token'
    assert extra.email == 'confirmed@test.com'
    assert extra.phone == '+70000000000'


def test_exchange_vk_code_error(mocker):
    # VK ID отклонил обмен (код истёк/использован/невалиден) → 401, не 5xx.
    mock_response = mocker.Mock()
    mock_response.json.return_value = {
        'error': 'invalid_grant',
        'error_description': 'code is expired',
    }
    mocker.patch('httpx.post', return_value=mock_response)

    with pytest.raises(HTTPException) as exc:
        exchange_vk_code('code', 'verifier', 'device')
    assert exc.value.status_code == 401


def test_exchange_vk_code_malformed(mocker):
    # Нет ни error, ни access_token — неожиданный ответ → сбой интеграции (5xx → Hawk).
    mock_response = mocker.Mock()
    mock_response.json.return_value = {'unexpected': True}
    mocker.patch('httpx.post', return_value=mock_response)

    with pytest.raises(VkResponseError):
        exchange_vk_code('code', 'verifier', 'device')


def test_get_extra_user_data_by_silent_token_success(mocker):
    from app.vk import get_extra_user_data_by_silent_token

    mock_response = mocker.Mock()
    mock_response.json.return_value = {
        'response': {'success': [{'email': 'test@test.com', 'phone': '+70000000000'}]}
    }
    mocker.patch('httpx.post', return_value=mock_response)

    data = get_extra_user_data_by_silent_token('silent', 'uuid')
    assert data.email == 'test@test.com'
    assert data.phone == '+70000000000'


def test_get_extra_user_data_by_silent_token_error(mocker):
    # VK явно вернул ошибку авторизации (протухший silent-токен) → 401.
    from app.vk import get_extra_user_data_by_silent_token

    mock_response = mocker.Mock()
    mock_response.json.return_value = {
        'response': {'errors': [{'code': 1, 'description': 'invalid token'}]}
    }
    mocker.patch('httpx.post', return_value=mock_response)

    with pytest.raises(HTTPException) as exc:
        get_extra_user_data_by_silent_token('silent', 'uuid')
    assert exc.value.status_code == 401


def test_get_extra_user_data_by_silent_token_no_success(mocker):
    # Структура валидна, но профиля нет → сбой интеграции (5xx → Hawk), не 401.
    from app.vk import get_extra_user_data_by_silent_token

    mock_response = mocker.Mock()
    mock_response.json.return_value = {'response': {}}
    mocker.patch('httpx.post', return_value=mock_response)

    with pytest.raises(VkResponseError):
        get_extra_user_data_by_silent_token('silent', 'uuid')


def test_get_extra_user_data_by_silent_token_malformed(mocker):
    from app.vk import get_extra_user_data_by_silent_token

    mock_response = mocker.Mock()
    mock_response.json.return_value = {'no_response': True}
    mocker.patch('httpx.post', return_value=mock_response)

    with pytest.raises(VkResponseError):
        get_extra_user_data_by_silent_token('silent', 'uuid')
