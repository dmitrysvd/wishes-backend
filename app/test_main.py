from dataclasses import dataclass
from datetime import date
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine, delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker
from starlette.status import HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND

from app.constants import Gender
from app.db import Base, User, Wish
from app.main import app, get_current_user, get_db
from app.utils import utc_now
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
        registered_at=utc_now(),
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
        registered_at=utc_now(),
    )
    db.add(_user)
    db.commit()
    yield _user
    db.delete(_user)
    db.commit()


@pytest.fixture
def third_user(db: Session):
    _user = User(
        display_name='Third test user',
        email='test3@mail.ru',
        photo_url='https://test_photo.com',
        vk_id='vk_id 3',
        vk_friends_data=[],
        vk_access_token='vk_access_token 3',
        firebase_uid='firebase uid 3',
        gender=Gender.male,
        registered_at=utc_now(),
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


@pytest.fixture(autouse=True)
def mock_external_requests(mocker):
    mocker.patch('app.utils.send_tg_channel_message')


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
        response = auth_client.get(f'users/{user.id}/wishes')
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

        mocker.patch('app.routers.auth.get_vk_user_data_by_access_token', _fake_get_data)
        mocker.patch('app.routers.auth.get_vk_user_friends', _fake_get_friends)

    @pytest.fixture(autouse=True)
    def mock_firebase_create_user(self, mocker):
        def _fake_create_user(*args, **kwargs):
            return 'firebase uid 2'

        mocker.patch('app.routers.auth.create_firebase_user', _fake_create_user)

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

        mocker.patch('app.routers.auth.get_firebase_user_data', _fake_get_user_data)

    @pytest.fixture(autouse=True)
    def mock_verify_id_token(self, mocker):
        def _fake_verify_id_token(id_token):
            return {
                'uid': 'uid',
            }

        mocker.patch('app.routers.auth.verify_id_token', _fake_verify_id_token)

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


def test_delete_user_with_wishes(
    auth_client: TestClient,
    user: User,
    wish: Wish,
    db: Session,
):
    db.delete(user)
    db.flush()
    assert not db.scalars(select(Wish).where(Wish.id == wish.id)).one_or_none()


class TestWishPermissions:
    def test_cannot_update_other_user_wish(
        self, auth_client: TestClient, other_user_wish: Wish
    ):
        response = auth_client.put(
            f'/wishes/{other_user_wish.id}',
            json={'name': 'Hacked name', 'description': '', 'price': None, 'link': None},
        )
        assert response.status_code == HTTP_403_FORBIDDEN

    def test_cannot_delete_other_user_wish(
        self, auth_client: TestClient, other_user_wish: Wish
    ):
        response = auth_client.delete(f'/wishes/{other_user_wish.id}')
        assert response.status_code == HTTP_403_FORBIDDEN

    def test_cannot_upload_image_to_other_user_wish(
        self, auth_client: TestClient, other_user_wish: Wish
    ):
        response = auth_client.post(
            f'/wishes/{other_user_wish.id}/image',
            files={'file': ('test.jpg', b'fake image content', 'image/jpeg')},
        )
        assert response.status_code == HTTP_403_FORBIDDEN

    def test_cannot_delete_image_from_other_user_wish(
        self, auth_client: TestClient, other_user_wish: Wish
    ):
        response = auth_client.delete(f'/wishes/{other_user_wish.id}/image')
        assert response.status_code == HTTP_403_FORBIDDEN


class TestReservationEdgeCases:
    def test_cannot_reserve_own_wish(
        self, auth_client: TestClient, wish: Wish, user: User
    ):
        response = auth_client.post(f'/wishes/{wish.id}/reserve')
        assert response.status_code == HTTP_403_FORBIDDEN

    def test_cannot_reserve_already_reserved_wish_by_other_user(
        self,
        auth_client: TestClient,
        db: Session,
        other_user_wish: Wish,
        third_user: User,
        user: User,
    ):
        # Резервируем желание третьим пользователем
        other_user_wish.reserved_by = third_user
        db.add(other_user_wish)
        db.commit()

        # Пытаемся зарезервировать его текущим пользователем
        response = auth_client.post(f'/wishes/{other_user_wish.id}/reserve')
        assert response.status_code == HTTP_403_FORBIDDEN

    def test_can_reserve_wish_again_if_already_reserved_by_me(
        self, auth_client: TestClient, db: Session, other_user_wish: Wish, user: User
    ):
        # Резервируем желание
        other_user_wish.reserved_by = user
        db.add(other_user_wish)
        db.commit()

        # Пытаемся зарезервировать его снова - должно пройти успешно (идемпотентность)
        response = auth_client.post(f'/wishes/{other_user_wish.id}/reserve')
        assert response.is_success

    def test_cannot_cancel_reservation_of_other_user(
        self,
        auth_client: TestClient,
        db: Session,
        other_user_wish: Wish,
        third_user: User,
    ):
        # Резервируем желание третьим пользователем
        other_user_wish.reserved_by = third_user
        db.add(other_user_wish)
        db.commit()

        # Пытаемся отменить резервацию текущим пользователем
        response = auth_client.post(f'/wishes/{other_user_wish.id}/cancel_reservation')
        assert response.status_code == HTTP_403_FORBIDDEN

    def test_can_cancel_own_reservation(
        self, auth_client: TestClient, db: Session, other_user_wish: Wish, user: User
    ):
        # Резервируем желание
        other_user_wish.reserved_by = user
        db.add(other_user_wish)
        db.commit()

        # Отменяем резервацию
        response = auth_client.post(f'/wishes/{other_user_wish.id}/cancel_reservation')
        assert response.is_success
        db.refresh(other_user_wish)
        assert other_user_wish.reserved_by is None

    def test_cannot_reserve_archived_wish(
        self, auth_client: TestClient, db: Session, other_user_wish: Wish
    ):
        # Архивируем желание
        other_user_wish.is_archived = True
        db.add(other_user_wish)
        db.commit()

        # Пытаемся зарезервировать архивное желание
        response = auth_client.post(f'/wishes/{other_user_wish.id}/reserve')
        assert response.status_code == HTTP_404_NOT_FOUND


class TestFollowUnfollow:
    def test_follow_user(
        self, auth_client: TestClient, db: Session, user: User, other_user: User
    ):
        response = auth_client.post(f'/follow/{other_user.id}')
        assert response.is_success
        db.refresh(user)
        assert other_user in user.follows

    def test_unfollow_user(
        self, auth_client: TestClient, db: Session, user: User, other_user: User
    ):
        # Сначала подписываемся
        user.follows.append(other_user)
        db.commit()

        # Затем отписываемся
        response = auth_client.post(f'/unfollow/{other_user.id}')
        assert response.is_success
        db.refresh(user)
        assert other_user not in user.follows

    def test_follow_already_followed_user_is_idempotent(
        self, auth_client: TestClient, db: Session, user: User, other_user: User
    ):
        # Подписываемся первый раз
        user.follows.append(other_user)
        db.commit()

        # Подписываемся повторно - должно быть идемпотентно
        response = auth_client.post(f'/follow/{other_user.id}')
        assert response.is_success
        db.refresh(user)
        assert user.follows.count(other_user) == 1

    def test_unfollow_not_followed_user_is_idempotent(
        self, auth_client: TestClient, db: Session, user: User, other_user: User
    ):
        # Отписываемся от пользователя, на которого не подписаны
        response = auth_client.post(f'/unfollow/{other_user.id}')
        assert response.is_success

    def test_get_followers(
        self, auth_client: TestClient, db: Session, user: User, other_user: User
    ):
        # other_user подписывается на user
        other_user.follows.append(user)
        db.commit()

        response = auth_client.get(f'/users/{user.id}/followers')
        assert response.is_success
        followers = response.json()
        assert len(followers) == 1
        assert followers[0]['id'] == str(other_user.id)

    def test_get_follows(
        self, auth_client: TestClient, db: Session, user: User, other_user: User
    ):
        # user подписывается на other_user
        user.follows.append(other_user)
        db.commit()

        response = auth_client.get(f'/users/{user.id}/follows')
        assert response.is_success
        follows = response.json()
        assert len(follows) == 1
        assert follows[0]['id'] == str(other_user.id)


class TestSearchEdgeCases:
    def test_search_empty_query(self, auth_client: TestClient):
        response = auth_client.get('/users/search', params={'q': ''})
        assert response.is_success
        assert response.json() == []

    def test_search_whitespace_query(self, auth_client: TestClient):
        response = auth_client.get('/users/search', params={'q': '   '})
        assert response.is_success
        assert response.json() == []

    def test_search_excludes_current_user(
        self, auth_client: TestClient, user: User
    ):
        # Ищем по имени текущего пользователя
        response = auth_client.get('/users/search', params={'q': user.display_name})
        assert response.is_success
        # Результаты не должны содержать текущего пользователя
        user_ids = [u['id'] for u in response.json()]
        assert str(user.id) not in user_ids


class TestArchivedWishAccess:
    def test_cannot_get_other_user_archived_wish(
        self, auth_client: TestClient, db: Session, other_user_wish: Wish
    ):
        # Архивируем чужое желание
        other_user_wish.is_archived = True
        db.add(other_user_wish)
        db.commit()

        # Пытаемся получить его
        response = auth_client.get(f'/wishes/{other_user_wish.id}')
        assert response.status_code == HTTP_404_NOT_FOUND

    def test_can_get_own_archived_wish(
        self, auth_client: TestClient, db: Session, wish: Wish
    ):
        # Архивируем своё желание
        wish.is_archived = True
        db.add(wish)
        db.commit()

        # Можем получить его
        response = auth_client.get(f'/wishes/{wish.id}')
        assert response.is_success
        assert response.json()['id'] == str(wish.id)


class TestWishImageUpload:
    def test_upload_wish_image(
        self, auth_client: TestClient, db: Session, wish: Wish
    ):
        response = auth_client.post(
            f'/wishes/{wish.id}/image',
            files={'file': ('test.jpg', b'fake image content', 'image/jpeg')},
        )
        assert response.is_success
        db.refresh(wish)
        assert wish.image is not None

    def test_delete_wish_image(
        self, auth_client: TestClient, db: Session, wish: Wish
    ):
        # Сначала устанавливаем изображение
        wish.image = 'some_image_hash'
        db.add(wish)
        db.commit()

        # Удаляем его
        response = auth_client.delete(f'/wishes/{wish.id}/image')
        assert response.is_success
        db.refresh(wish)
        assert wish.image is None


class TestWishCRUD:
    def test_create_wish(self, auth_client: TestClient, db: Session, user: User):
        response = auth_client.post(
            '/wishes',
            json={
                'name': 'New wish',
                'description': 'Description',
                'price': 100,
                'link': 'https://example.com',
            },
        )
        assert response.is_success, response.json()
        wish_data = response.json()
        assert wish_data['name'] == 'New wish'
        assert wish_data['description'] == 'Description'

        # Проверяем, что желание создано в БД
        wish_id = UUID(wish_data['id'])
        wish = db.scalars(select(Wish).where(Wish.id == wish_id)).one()
        assert wish.user_id == user.id

    def test_update_wish(self, auth_client: TestClient, db: Session, wish: Wish):
        response = auth_client.put(
            f'/wishes/{wish.id}',
            json={
                'name': 'Updated name',
                'description': 'Updated description',
                'price': '200.00',
                'link': 'https://updated.com',
            },
        )
        assert response.is_success
        db.refresh(wish)
        assert wish.name == 'Updated name'
        assert wish.description == 'Updated description'

    def test_delete_wish(self, auth_client: TestClient, db: Session, wish: Wish):
        wish_id = wish.id
        response = auth_client.delete(f'/wishes/{wish_id}')
        assert response.is_success
        assert not db.scalars(select(Wish).where(Wish.id == wish_id)).one_or_none()
