"""Тесты формы контракта фичи 0018 (гостевой резерв на публичном вишлисте)."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.constants import GUEST_COOKIE_NAME
from app.db import User
from app.main import app
from app.utils import utc_now


@pytest.fixture
def owner(db) -> User:
    user = User(display_name='Owner', firebase_uid='uid-owner', registered_at=utc_now())
    db.add(user)
    db.commit()
    return user


_GUEST_OPS = (
    '/public/users/{user_id}/wishes/{wish_id}/reserve',
    '/public/users/{user_id}/wishes/{wish_id}/cancel_reservation',
)


def test_public_wishlist_is_not_cacheable(api_client: TestClient, owner: User):
    """Ответ зависит от куки гостя — прокси и браузер его не кешируют."""
    response = api_client.get(f'/public/users/{owner.id}/wishlist')
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert response.headers['Vary'] == 'Cookie'


@pytest.mark.parametrize('path', _GUEST_OPS)
def test_guest_ops_are_public(api_client: TestClient, owner: User, path: str):
    """Без `Authorization` — не 401: неизвестная хотелка даёт свой 410."""
    url = path.format(user_id=owner.id, wish_id=uuid4())
    assert api_client.post(url).status_code == 410


@pytest.mark.parametrize('path', _GUEST_OPS)
def test_guest_ops_validate_path(api_client: TestClient, path: str):
    url = path.format(user_id='not-a-uuid', wish_id=uuid4())
    assert api_client.post(url).status_code == 422


def test_guest_ops_are_public_and_declare_statuses():
    openapi = app.openapi()
    expected = {
        _GUEST_OPS[0]: {'200', '404', '409', '410', '422', '429'},
        _GUEST_OPS[1]: {'200', '403', '404', '410', '422'},
    }
    for path, codes in expected.items():
        operation = openapi['paths'][path]['post']
        assert operation['security'] == []
        assert set(operation['responses']) == codes
        cookie = [p for p in operation['parameters'] if p['in'] == 'cookie']
        assert [p['name'] for p in cookie] == [GUEST_COOKIE_NAME]


def test_public_wishlist_is_public_with_reserved_by_me():
    openapi = app.openapi()
    assert openapi['paths']['/public/users/{user_id}/wishlist']['get']['security'] == []
    wish = openapi['components']['schemas']['PublicWishSchema']
    assert 'reserved_by_me' in wish['required']


def test_auth_responses_carry_guest_merge_fields():
    openapi = app.openapi()
    schemas = openapi['components']['schemas']
    for name in ('AuthFirebaseResponseSchema', 'ResponseVkAuthMobileSchema'):
        required = set(schemas[name]['required'])
        assert {'guest_merged_reservations', 'guest_followed_owner_ids'} <= required
    for path in ('/auth/firebase', '/auth/vk/vkid'):
        operation = openapi['paths'][path]['post']
        cookie = [p['name'] for p in operation['parameters'] if p['in'] == 'cookie']
        assert cookie == [GUEST_COOKIE_NAME]
        assert 'Set-Cookie' in operation['responses']['200']['headers']


def test_reservation_push_payload_same_for_guest_and_app():
    """Гостевой резерв даёт владельцу ровно тот же пуш, что обычный."""
    paths = app.openapi()['paths']
    guest = paths[_GUEST_OPS[0]]['post']['x-push-payload']
    regular = paths['/wishes/{wish_id}/reserve']['post']['x-push-payload']
    assert guest == regular
    assert 'delivery_id' in guest['data']


def test_app_reserve_declares_conflict_and_cancel_codes():
    paths = app.openapi()['paths']
    reserve = paths['/wishes/{wish_id}/reserve']['post']['responses']
    cancel = paths['/wishes/{wish_id}/cancel_reservation']['post']['responses']
    assert {'403', '404', '409'} <= set(reserve)
    assert {'403', '404'} <= set(cancel)
