import pytest
from fastapi.testclient import TestClient

from app.db import PushInstallation, User
from app.main import app, get_current_user, get_db
from app.utils import utc_now


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
    # `fid: null` — как опущен (сериализаторы клиентов пишут null по умолчанию).
    response = auth_client.post(
        '/save_push_token', json={'push_token': 'new_push_token', 'fid': None}
    )
    assert response.status_code == 200
    db.refresh(user)
    assert [i.push_token for i in user.push_installations] == ['new_push_token']
    assert user.firebase_push_token is None  # DEPRECATED-колонку не пишем


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


# --- 0016: форма save_push_token (остаётся после реализации) ---


def test_save_push_token_form(auth_client):
    url = '/save_push_token'
    assert auth_client.post(url, json={}).status_code == 422
    assert auth_client.post(url, json={'fid': 'x'}).status_code == 422
    assert auth_client.post(url, json={'push_token': ''}).status_code == 422
    assert auth_client.post(url, json={'push_token': 't', 'fid': ''}).status_code == 422


def test_save_push_token_is_protected(api_client):
    # В этом модуле auth подменён autouse-фикстурой — снимаем подмену для 401.
    app.dependency_overrides.pop(get_current_user)
    assert (
        api_client.post('/save_push_token', json={'push_token': 't'}).status_code == 401
    )


def test_save_push_token_upsert(auth_client, db, user):
    url = '/save_push_token'
    # Старый клиент: только токен → установка без FID.
    assert auth_client.post(url, json={'push_token': 'T1'}).status_code == 200
    # Тот же телефон обновился: найдена по токену, дорастает до FID.
    assert (
        auth_client.post(url, json={'push_token': 'T1', 'fid': 'F'}).status_code == 200
    )
    db.expire_all()
    (installation,) = user.push_installations
    assert (installation.fid, installation.push_token) == ('F', 'T1')
    # Ротация токена у нового клиента: найдена по FID, токен перезаписан.
    assert (
        auth_client.post(url, json={'push_token': 'T2', 'fid': 'F'}).status_code == 200
    )
    db.expire_all()
    (installation,) = user.push_installations
    assert (installation.fid, installation.push_token) == ('F', 'T2')
    # Второе устройство — вторая установка.
    assert (
        auth_client.post(url, json={'push_token': 'T3', 'fid': 'G'}).status_code == 200
    )
    db.expire_all()
    assert sorted(i.fid for i in user.push_installations) == ['F', 'G']


def test_save_push_token_moves_installation_between_users(auth_client, db, user):
    other = User(
        display_name='Other',
        firebase_uid='other_uid',
        registered_at=utc_now(),
        push_installations=[PushInstallation(fid='F', push_token='T1')],
    )
    db.add(other)
    db.commit()

    # A вышел, B (текущий юзер) вошёл на том же устройстве.
    resp = auth_client.post('/save_push_token', json={'push_token': 'T1', 'fid': 'F'})

    assert resp.status_code == 200
    db.expire_all()
    assert other.push_installations == []
    assert [i.fid for i in user.push_installations] == ['F']


def test_save_push_token_drops_stale_row_with_same_token(db, user):
    # Строка без FID с токеном T2 (старый клиент) и строка с FID F/T1; новый
    # клиент присылает F+T2 — токен уникален, старая строка без FID снимается.
    from app.push_installations import upsert_push_installation

    db.add_all(
        [
            PushInstallation(user_id=user.id, push_token='T2'),
            PushInstallation(user_id=user.id, fid='F', push_token='T1'),
        ]
    )
    db.commit()

    upsert_push_installation(db, user, fid='F', push_token='T2')
    db.commit()
    db.expire_all()
    assert [(i.fid, i.push_token) for i in user.push_installations] == [('F', 'T2')]
