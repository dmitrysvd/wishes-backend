import pytest
from fastapi.testclient import TestClient

from app.db import User
from app.main import app, get_current_user, get_db


@pytest.fixture
def user(db):
    from app.constants import Gender
    from app.utils import utc_now

    _user = User(
        display_name='Test user',
        email='test_extra@mail.ru',
        firebase_uid='firebase uid extra',
        gender=Gender.male,
        registered_at=utc_now(),
    )
    db.add(_user)
    db.commit()
    return _user


@pytest.fixture(autouse=True)
def override_dependencies(user, db):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    yield
    app.dependency_overrides = {}


@pytest.fixture
def auth_client() -> TestClient:
    return TestClient(app, headers={'Authorization': 'Bearer test_token'})


def test_auth_firebase_existing_user_update_uid(mocker, db):

    from app.routers.auth import auth_firebase
    from app.schemas import RequestFirebaseAuthSchema
    from app.utils import utc_now

    user = User(
        display_name='Old',
        firebase_uid='old_uid',
        email='test@test.com',
        registered_at=utc_now(),
    )
    db.add(user)
    db.commit()

    mocker.patch('app.routers.auth.verify_id_token', return_value={'uid': 'new_uid'})
    mock_get_data = mocker.patch('app.routers.auth.get_firebase_user_data')
    mock_get_data.return_value.email_verified = True
    mock_get_data.return_value.email = 'test@test.com'

    auth_firebase(RequestFirebaseAuthSchema(id_token='token'), db)

    db.refresh(user)
    assert user.firebase_uid == 'new_uid'


def test_save_push_token(auth_client, db, user):
    response = auth_client.post(
        '/save_push_token', json={'push_token': 'new_push_token'}
    )
    assert response.status_code == 200
    db.refresh(user)
    assert user.firebase_push_token == 'new_push_token'


def test_users_router_extra_coverage(auth_client, mocker):
    from httpx import HTTPError

    from app.parsers import ItemInfoParseError

    # Case 1: result is None
    mocker.patch('app.routers.users.try_parse_item_by_link', return_value=None)
    response = auth_client.post(
        '/item_info_from_page', json={'link': 'https://example.com'}
    )
    assert response.status_code == 400

    # Case 2: ItemInfoParseError then retry fail
    mocker.patch(
        'app.routers.users.try_parse_item_by_link',
        side_effect=ItemInfoParseError('fail'),
    )
    response = auth_client.post(
        '/item_info_from_page',
        json={'link': 'https://example.com', 'html': 'some html'},
    )
    assert response.status_code == 400

    # Case 3: HTTPError
    mocker.patch(
        'app.routers.users.try_parse_item_by_link',
        side_effect=HTTPError('http error'),
    )
    response = auth_client.post(
        '/item_info_from_page', json={'link': 'https://example.com'}
    )
    assert response.status_code == 400


def test_delete_own_account(auth_client, mocker, db, user):
    mock_delete_fb = mocker.patch('app.routers.users.delete_firebase_user')
    response = auth_client.post('/delete_own_account')
    assert response.status_code == 200
    mock_delete_fb.assert_called_once_with(user.firebase_uid)

    from sqlalchemy import select

    assert db.scalars(select(User).where(User.id == user.id)).one_or_none() is None
