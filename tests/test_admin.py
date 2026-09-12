from unittest.mock import AsyncMock

import pytest
from fastapi import Request

from app.admin.setup import AdminAuth


@pytest.mark.anyio
async def test_admin_auth_login_success(mocker):
    mocker.patch('app.admin.setup.settings.ADMIN_PASSWORD', 'correct_password')
    auth = AdminAuth(secret_key='secret')

    # Mock request.form()
    mock_request = mocker.Mock(spec=Request)
    mock_request.form = AsyncMock(
        return_value={'username': 'admin', 'password': 'correct_password'}
    )
    mock_request.session = {}

    result = await auth.login(mock_request)
    assert result is True
    assert mock_request.session.get('has_admin_access') is True


@pytest.mark.anyio
async def test_admin_auth_login_fail(mocker):
    mocker.patch('app.admin.setup.settings.ADMIN_PASSWORD', 'correct_password')
    auth = AdminAuth(secret_key='secret')

    mock_request = mocker.Mock(spec=Request)
    mock_request.form = AsyncMock(
        return_value={'username': 'admin', 'password': 'wrong_password'}
    )
    mock_request.session = {}

    result = await auth.login(mock_request)
    assert result is False
    assert 'has_admin_access' not in mock_request.session


@pytest.mark.anyio
async def test_admin_auth_logout(mocker):
    auth = AdminAuth(secret_key='secret')
    mock_request = mocker.Mock(spec=Request)
    mock_request.session = {'has_admin_access': True}

    result = await auth.logout(mock_request)
    assert result is True
    assert mock_request.session == {}


@pytest.mark.anyio
async def test_admin_auth_authenticate(mocker):
    auth = AdminAuth(secret_key='secret')

    # Authorized
    mock_request_ok = mocker.Mock(spec=Request)
    mock_request_ok.session = {'has_admin_access': True}
    assert await auth.authenticate(mock_request_ok) is True

    # Unauthorized
    mock_request_fail = mocker.Mock(spec=Request)
    mock_request_fail.session = {}
    assert await auth.authenticate(mock_request_fail) is False


def test_url_root_path_trailing_slash_is_stripped():
    # '/' у root_path ломает Mount-ы (/admin, /static): Starlette срезает его с
    # начала пути и у саб-приложения пропадает ведущий слэш. Нормализуем в ''.
    from app.config import Settings

    assert Settings.model_validate({'URL_ROOT_PATH': '/'}).URL_ROOT_PATH == ''
    assert Settings.model_validate({'URL_ROOT_PATH': '/api/'}).URL_ROOT_PATH == '/api'


@pytest.fixture
def observed_wish(test_engine):
    """Хотелка с одним наблюдением, закоммиченная в тестовую БД.

    Админка ходит в БД своими соединениями и не видит незакоммиченную транзакцию
    фикстуры `db`, поэтому пишем через движок и убираем за собой.
    """
    from datetime import date
    from uuid import uuid4

    from sqlalchemy.orm import Session

    from app.constants import PriceObservationStatus, Shop
    from app.db import User, Wish, WishPriceObservation
    from app.utils import utc_now

    with Session(test_engine) as session:
        user = User(
            firebase_uid=f'uid-{uuid4()}',
            display_name='Тест',
            email=f'{uuid4()}@t.ru',
            registered_at=utc_now(),
        )
        session.add(user)
        session.flush()
        wish = Wish(user_id=user.id, name='Зонт', link='https://wildberries.ru/1')
        session.add(wish)
        session.flush()
        session.add(
            WishPriceObservation(
                wish_id=wish.id,
                observed_date=date(2026, 9, 12),
                shop=Shop.wildberries,
                sku=1,
                status=PriceObservationStatus.sold_out,
            )
        )
        session.commit()
        wish_id, user_id = wish.id, user.id
    yield wish_id
    with Session(test_engine) as session:
        session.delete(session.get(User, user_id))  # каскадом уходят wish и наблюдение
        session.commit()


def test_price_observation_admin_list_links_to_wish(test_engine, mocker, observed_wish):
    # Отдельный app с админкой на тестовом движке: `app.main.app` держит админку
    # на боевом `engine`, который в тестах закрыт.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.admin.setup import setup_admin

    mocker.patch('app.admin.setup.settings.ADMIN_PASSWORD', 'pwd')
    admin_app = FastAPI()
    setup_admin(admin_app, test_engine)
    client = TestClient(admin_app)
    client.post('/admin/login', data={'username': 'admin', 'password': 'pwd'})

    response = client.get('/admin/wish-price-observation/list?search=1')

    assert response.status_code == 200
    assert 'Price Observations' in response.text
    # Колонка хотелки — ссылка на её карточку в админке, а не голый UUID.
    assert f'/admin/wish/details/{observed_wish}' in response.text


@pytest.fixture
def admin_client(test_engine, mocker):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.admin.setup import setup_admin

    mocker.patch('app.admin.setup.settings.ADMIN_PASSWORD', 'pwd')
    admin_app = FastAPI()
    setup_admin(admin_app, test_engine)
    client = TestClient(admin_app)
    client.post('/admin/login', data={'username': 'admin', 'password': 'pwd'})
    return client


def test_price_observation_status_filter(admin_client, observed_wish):
    url = '/admin/wish-price-observation/list'
    wish_link = f'/admin/wish/details/{observed_wish}'

    assert wish_link in admin_client.get(f'{url}?status=sold_out').text
    assert wish_link not in admin_client.get(f'{url}?status=ok').text
    assert wish_link in admin_client.get(f'{url}?status=').text


def test_wish_from_recommendation_filter(admin_client, observed_wish):
    url = '/admin/wish/list'
    wish_link = f'/admin/wish/details/{observed_wish}'

    # Хотелка из фикстуры создана без рекомендации.
    assert wish_link in admin_client.get(f'{url}?recommendation_id=false').text
    assert wish_link not in admin_client.get(f'{url}?recommendation_id=true').text
    assert wish_link in admin_client.get(f'{url}?recommendation_id=all').text


def test_new_filters_apply(admin_client, observed_wish):
    wish_link = f'/admin/wish/details/{observed_wish}'
    wish_list = '/admin/wish/list'
    obs_list = '/admin/wish-price-observation/list'

    # Хотелка из фикстуры: не архивная, не зарезервирована, без цены.
    assert wish_link in admin_client.get(f'{wish_list}?is_archived=false').text
    assert wish_link not in admin_client.get(f'{wish_list}?is_archived=true').text
    assert wish_link in admin_client.get(f'{wish_list}?reserved_by_id=false').text
    assert wish_link not in admin_client.get(f'{wish_list}?price=true').text
    assert wish_link in admin_client.get(f'{obs_list}?shop=wildberries').text

    # Юзер из фикстуры: не тестовый, без VK и без push-токена.
    users = admin_client.get('/admin/user/list?is_test=false&vk_id=false').text
    assert 'Тест' in users
    assert (
        'Тест' not in admin_client.get('/admin/user/list?firebase_push_token=true').text
    )
