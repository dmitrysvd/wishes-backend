from dataclasses import dataclass
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine, delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.constants import Gender
from app.db import Base, User, Wish
from app.main import app, get_current_user, get_db
from app.vk import Gender, VkUserBasicData, VkUserExtraData

engine = create_engine(
    'sqlite://',
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def db():
    _db = TestingSessionLocal()
    Base.metadata.create_all(bind=engine)
    try:
        yield _db
    finally:
        _db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def user(db: Session):
    _user = User(
        display_name='Test user',
        email='test@mail.ru',
        photo_url='https://test_photo.com',
        vk_id='vk_id',
        vk_friends_data=[],
        vk_access_token='vk_access_token',
        firebase_uid='firebase uid',
        gender=Gender.male,
    )
    db.add(_user)
    db.commit()
    yield _user
    db.delete(_user)
    db.commit()


@pytest.fixture
def other_user(db: Session):
    _user = User(
        display_name='Other test user',
        email='test2@mail.ru',
        photo_url='https://test_photo.com',
        vk_id='vk_id 2',
        vk_friends_data=[],
        vk_access_token='vk_access_token 2',
        firebase_uid='firebase uid 2',
        gender=Gender.male,
    )
    db.add(_user)
    db.commit()
    yield _user
    db.delete(_user)
    db.commit()


@pytest.fixture
def wish(db: Session, user: User):
    _wish = Wish(
        user_id=user.id,
        name='name',
    )
    db.add(_wish)
    db.commit()
    yield _wish
    db.delete(_wish)
    db.commit()


@pytest.fixture
def other_user_wish(db: Session, other_user: User) -> Wish:
    _wish = Wish(
        user_id=other_user.id,
        name='name',
    )
    db.add(_wish)
    db.commit()
    return _wish


@pytest.fixture(autouse=True)
def override_dependencies(user, db):
    def override_get_db():
        return db

    def override_get_current_user():
        return user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    yield
    app.dependency_overrides = {}


@pytest.fixture
def api_client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth_client(user: User) -> TestClient:
    client = TestClient(app, headers={'Authorization': ''})
    return client


class TestMyWishes:
    def test_empty_wishes(self, auth_client):
        response = auth_client.get('/wishes')
        assert response.is_success
        assert response.json() == []

    def test_list_wishes(self, auth_client: TestClient, wish: Wish):
        response = auth_client.get('/wishes')
        assert response.is_success
        assert [w['id'] for w in response.json()] == [str(wish.id)]

    def test_get_single_wish(self, auth_client: TestClient, wish: Wish):
        response = auth_client.get(f'/wishes/{wish.id}')
        assert response.is_success
        assert response.json()['id'] == str(wish.id)

    def test_get_user_wishes(self, auth_client: TestClient, wish: Wish, user: User):
        response = auth_client.get(f'/users/{user.id}/wishes/')
        assert response.is_success
        assert [w['id'] for w in response.json()] == [str(wish.id)]


class TestReservedWishes:
    @pytest.fixture
    def reserved_wish(self, db: Session, wish: Wish, user: User, other_user: User):
        db.execute(
            update(Wish)
            .where(Wish.id == wish.id)
            .values(reserved_by_id=user.id, user_id=other_user.id)
        )
        db.commit()
        return wish

    def test_list_reserved_wishes(self, auth_client: TestClient, reserved_wish: Wish):
        response = auth_client.get('/reserved_wishes')
        assert response.is_success
        assert [w['id'] for w in response.json()] == [str(reserved_wish.id)]

    def test_reserve_wish(
        self,
        auth_client: TestClient,
        other_user_wish: Wish,
        db: Session,
        user: User,
    ):
        response = auth_client.post(f'/wishes/{other_user_wish.id}/reserve')
        assert response.is_success
        assert (
            db.scalars(select(Wish).where(Wish.id == other_user_wish.id))
            .one()
            .reserved_by_id
            == user.id
        )


class TestMyUser:
    def test_get_user(self, user: User, auth_client: TestClient, db: Session):
        response = auth_client.get('/users/me')
        assert response.is_success
        assert response.json()['id'] == str(user.id)

    def test_update_name(self, user: User, auth_client: TestClient, db: Session):
        response = auth_client.put(
            '/users/me',
            json={
                'display_name': 'New name',
                'gender': 'male',
                'birth_date': '2000-01-01',
            },
        )
        assert response.is_success, response.json()
        user = db.scalars(select(User).where(User.id == user.id)).one()
        assert user.display_name == 'New name'


class TestArchiveWish:
    @pytest.fixture
    def archived_wish(self, db: Session, wish: Wish):
        db.execute(update(Wish).where(Wish.id == wish.id).values(is_archived=True))
        db.commit()
        return wish

    def test_archive(self, auth_client: TestClient, db: Session, wish: Wish):
        response = auth_client.post(f'/wishes/{wish.id}/archive')
        assert response.is_success, response.json()
        assert db.scalars(select(Wish).where(Wish.id == wish.id)).one().is_archived

    def test_unarchive(self, auth_client: TestClient, db: Session, archived_wish: Wish):
        response = auth_client.post(f'/wishes/{archived_wish.id}/unarchive')
        assert response.is_success, response.json()
        assert (
            not db.scalars(select(Wish).where(Wish.id == archived_wish.id))
            .one()
            .is_archived
        )

    def test_read_archived(
        self, auth_client: TestClient, db: Session, archived_wish: Wish
    ):
        response = auth_client.get(f'/archived_wishes')
        assert response.is_success, response.json()
        assert str(archived_wish.id) in [
            wish_data['id'] for wish_data in response.json()
        ]


class TestAuth:
    FIREBASE_USER_EMAIL = 'test_firebase_email@mail.com'

    @pytest.fixture(autouse=True)
    def mock_vk_request(self, mocker):
        def _fake_get_data(access_token):
            return VkUserBasicData(
                id=12345678,
                first_name='Иванов',
                last_name='Иван',
                photo_url='https://photo.ru',
                gender=Gender.male,
                birthdate=date.fromisoformat('2020-01-01'),
            )

        def _fake_get_friends(access_token):
            return []

        mocker.patch('app.main.get_vk_user_data_by_access_token', _fake_get_data)
        mocker.patch('app.main.get_vk_user_friends', _fake_get_friends)

    @pytest.fixture(autouse=True)
    def mock_firebase_create_user(self, mocker):
        def _fake_create_user(*args, **kwargs):
            return 'firebase uid 2'

        mocker.patch('app.main.create_firebase_user', _fake_create_user)

    @pytest.fixture(autouse=True)
    def mock_firebase_get_user_data(self, mocker):
        def _fake_get_user_data(uid: str):
            @dataclass
            class FakeUserRecord:
                email_verified: bool
                email: str
                display_name: str
                photo_url: str
                phone_number: str

            return FakeUserRecord(
                email_verified=True,
                email=self.FIREBASE_USER_EMAIL,
                display_name='Иванов Иван',
                photo_url='https://photo.com',
                phone_number='8999334424242',
            )

        mocker.patch('app.main.get_firebase_user_data', _fake_get_user_data)

    @pytest.fixture(autouse=True)
    def mock_verify_id_token(self, mocker):
        def _fake_verify_id_token(id_token):
            return {
                'uid': 'uid',
            }

        mocker.patch('app.main.verify_id_token', _fake_verify_id_token)

    def test_auth_vk_success(
        self,
        api_client: TestClient,
        auth_client: TestClient,
        db: Session,
    ):
        response = api_client.post(
            '/auth/vk/mobile',
            json={
                'access_token': 'some_token',
                'email': 'test_vk@test.com',
                'phone': '+79898041180',
            },
        )
        assert response.is_success
        user = db.scalars(
            select(User).where(User.vk_access_token == 'some_token')
        ).one_or_none()
        assert user is not None
        assert user.email == 'test_vk@test.com'
        assert api_client.post(
            '/users/me',
        )

        assert auth_client.get(f'/users/{user.id}').is_success

    def test_auth_vk_no_email_phone(
        self,
        api_client: TestClient,
        auth_client: TestClient,
        db: Session,
    ):
        response = api_client.post(
            '/auth/vk/mobile',
            json={
                'access_token': 'some_vk_token',
                'email': None,
                'phone': None,
            },
        )
        assert response.is_success
        user = db.scalars(
            select(User).where(User.vk_access_token == 'some_vk_token')
        ).one_or_none()
        assert user is not None
        assert user.email is None

        assert auth_client.get(f'/users/{user.id}').is_success

    def test_auth_firebase(
        self,
        api_client: TestClient,
        db: Session,
    ):
        response = api_client.post(
            '/auth/firebase',
            json={'id_token': 'id_token'},
        )
        assert response.is_success

    def test_auth_vk_after_firebase_with_same_email(
        self,
        api_client: TestClient,
        db: Session,
    ):
        response = api_client.post(
            '/auth/firebase',
            json={'id_token': 'id_token'},
        )
        assert response.is_success

        response = api_client.post(
            '/auth/vk/mobile',
            json={
                'access_token': 'some_vk_token',
                'email': self.FIREBASE_USER_EMAIL,
                'phone': None,
            },
        )
        assert response.is_success

        assert (
            db.scalars(
                select(func.count(User.id)).where(
                    User.email == self.FIREBASE_USER_EMAIL
                )
            ).one()
            == 1
        )


def test_search_user(
    auth_client: TestClient,
    other_user: User,
):
    response = auth_client.get(f'/users/search', params={'q': 'Other test user'})
    assert response.is_success, response.json()
    response_data = response.json()
    assert len(response_data) == 1
    assert response_data[0]['email'] == 'test2@mail.ru'


def test_delete_user_with_wishes(
    auth_client: TestClient,
    user: User,
    wish: Wish,
    db: Session,
):
    db.delete(user)
    db.flush()
    assert not db.scalars(select(Wish).where(Wish.id == wish.id)).one_or_none()
