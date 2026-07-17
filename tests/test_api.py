from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from firebase_admin.exceptions import AlreadyExistsError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import Gender
from app.db import User, UserAttribution, Wish, WishRecommendation
from app.main import app, get_current_user, get_db
from app.utils import utc_now
from app.vk import VkUserBasicData, VkUserExtraData


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
    return _user


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
    return _user


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
    return _user


@pytest.fixture
def wish(db: Session, user: User):
    _wish = Wish(
        user_id=user.id,
        name='name',
    )
    db.add(_wish)
    db.commit()
    return _wish


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
    client = TestClient(app, headers={'Authorization': 'Bearer test_token'})
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
        response = auth_client.get(f'/users/{user.id}/wishes')
        assert response.is_success
        assert [w['id'] for w in response.json()] == [str(wish.id)]


class TestPublicWishlist:
    """Публичный веб-вишлист: открывается без авторизации, без PII владельца."""

    def test_returns_owner_and_active_wishes(
        self,
        api_client: TestClient,
        db: Session,
        other_user: User,
    ):
        other_user.birth_date = date(1990, 3, 15)
        with_image = Wish(user_id=other_user.id, name='with image', image='abc')
        without_image = Wish(user_id=other_user.id, name='no image')
        reserved = Wish(user_id=other_user.id, name='reserved', reserved_by_id=None)
        archived = Wish(user_id=other_user.id, name='archived', is_archived=True)
        db.add_all([with_image, without_image, reserved, archived])
        db.commit()

        response = api_client.get(f'/public/users/{other_user.id}/wishlist')
        assert response.is_success
        data = response.json()

        # Владелец отдаётся без PII: email/телефон/год рождения.
        assert data['owner']['id'] == str(other_user.id)
        assert data['owner']['display_name'] == other_user.display_name
        assert 'email' not in data['owner']
        assert 'phone' not in data['owner']
        # ДР — только день и месяц, без года.
        assert data['owner']['birthday'] == {'day': 15, 'month': 3}

        # Архивные хотелки в публичный список не попадают.
        names = {w['name'] for w in data['wishes']}
        assert names == {'with image', 'no image', 'reserved'}

        by_name = {w['name']: w for w in data['wishes']}
        assert by_name['with image']['image_url'] == '/media/wish_images/abc'
        assert by_name['no image']['image_url'] is None
        # Личность зарезервировавшего не раскрывается — только флаг.
        assert all('reserved_by_id' not in w for w in data['wishes'])
        assert by_name['no image']['is_reserved'] is False

    def test_owner_without_birth_date_has_null_birthday(
        self,
        api_client: TestClient,
        db: Session,
        other_user: User,
    ):
        other_user.birth_date = None
        db.commit()

        response = api_client.get(f'/public/users/{other_user.id}/wishlist')
        assert response.is_success
        assert response.json()['owner']['birthday'] is None
        assert response.json()['wishes'] == []

    def test_reserved_flag_true(
        self,
        api_client: TestClient,
        db: Session,
        user: User,
        other_user: User,
    ):
        wish = Wish(user_id=other_user.id, name='taken', reserved_by_id=user.id)
        db.add(wish)
        db.commit()

        response = api_client.get(f'/public/users/{other_user.id}/wishlist')
        assert response.is_success
        taken = response.json()['wishes'][0]
        assert taken['is_reserved'] is True

    def test_unknown_user_returns_404(self, api_client: TestClient):
        response = api_client.get(f'/public/users/{uuid4()}/wishlist')
        assert response.status_code == 404


class TestOgHelpers:
    """Чистые функции сборки Open Graph-превью."""

    @pytest.mark.parametrize(
        ('count', 'expected'),
        [
            (1, '1 желание'),
            (2, '2 желания'),
            (4, '4 желания'),
            (5, '5 желаний'),
            (0, '0 желаний'),
            (11, '11 желаний'),
            (12, '12 желаний'),
            (14, '14 желаний'),
            (21, '21 желание'),
            (22, '22 желания'),
            (25, '25 желаний'),
            (111, '111 желаний'),
        ],
    )
    def test_pluralize_wishes(self, count: int, expected: str):
        from app.helpers.og_helpers import pluralize_wishes

        assert pluralize_wishes(count) == expected

    def test_absolutize_url_passthrough_and_prefix(self):
        from app.helpers.og_helpers import absolutize_url

        assert absolutize_url('https://cdn/x.png') == 'https://cdn/x.png'
        assert absolutize_url('http://cdn/x.png') == 'http://cdn/x.png'
        assert absolutize_url('/static/og_banner.png') == (
            'https://hotelki.pro/static/og_banner.png'
        )

    def test_format_birthday(self):
        from app.helpers.og_helpers import format_birthday

        assert format_birthday(date(1990, 7, 5)) == '5 июля'


class TestOgPreview:
    """Серверный HTML с OG-тегами для краулеров соцсетей по deep link /user."""

    def test_user_with_photo_birthday_and_wishes(
        self, api_client: TestClient, db: Session, other_user: User
    ):
        other_user.photo_url = 'https://cdn/photo.png'
        other_user.birth_date = date(1990, 7, 5)
        db.add_all(
            [
                Wish(user_id=other_user.id, name='one'),
                Wish(user_id=other_user.id, name='two'),
                Wish(user_id=other_user.id, name='archived', is_archived=True),
            ]
        )
        db.commit()

        response = api_client.get(f'/og/user?userId={other_user.id}')
        assert response.is_success
        html = response.text
        # Заголовок — имя владельца; картинка — фото профиля (лицо = CTR).
        assert 'Хотелки · Other test user' in html
        assert 'content="https://cdn/photo.png"' in html
        # Архивная не считается → «2 желания», + дата ДР без года.
        assert '2 желания · ДР 5 июля' in html
        assert f'content="https://hotelki.pro/user?userId={other_user.id}"' in html

    def test_user_without_photo_falls_back_to_brand_banner(
        self, api_client: TestClient, db: Session, other_user: User
    ):
        other_user.photo_url = None
        other_user.birth_date = None
        db.commit()

        response = api_client.get(f'/og/user?userId={other_user.id}')
        assert response.is_success
        # Нет фото → бренд-баннер; нет ДР и хотелок → «Список желаний».
        assert 'content="https://hotelki.pro/static/og_banner.png"' in response.text
        assert 'content="Список желаний"' in response.text

    def test_unknown_user_id_renders_brand_fallback(self, api_client: TestClient):
        response = api_client.get(f'/og/user?userId={uuid4()}')
        assert response.is_success
        html = response.text
        assert 'content="Список желаний по ссылке — узнай, что подарить"' in html
        assert 'content="https://hotelki.pro/static/og_banner.png"' in html

    def test_invalid_user_id_renders_brand_fallback(self, api_client: TestClient):
        response = api_client.get('/og/user?userId=not-a-uuid')
        assert response.is_success
        assert 'content="https://hotelki.pro/static/og_banner.png"' in response.text

    def test_missing_user_id_renders_brand_fallback(self, api_client: TestClient):
        response = api_client.get('/og/user')
        assert response.is_success
        assert 'content="https://hotelki.pro/static/og_banner.png"' in response.text


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
        db.refresh(other_user_wish)
        assert other_user_wish.reserved_by_id == user.id


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
        db.refresh(user)
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
        db.refresh(wish)
        assert wish.is_archived

    def test_unarchive(self, auth_client: TestClient, db: Session, archived_wish: Wish):
        response = auth_client.post(f'/wishes/{archived_wish.id}/unarchive')
        assert response.is_success, response.json()
        db.refresh(archived_wish)
        assert not archived_wish.is_archived

    def test_read_archived(
        self, auth_client: TestClient, db: Session, archived_wish: Wish
    ):
        response = auth_client.get('/archived_wishes')
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

        mocker.patch(
            'app.routers.auth.get_vk_user_data_by_access_token', _fake_get_data
        )
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

    def test_auth_firebase(
        self,
        api_client: TestClient,
        db: Session,
    ):
        response = api_client.post(
            '/auth/firebase',
            json={'id_token': 'id_token'},
        )
        assert response.status_code == 200
        assert response.content == b''
        user = db.scalars(select(User).where(User.firebase_uid == 'uid')).one()
        assert user.display_name == 'Иванов Иван'

    def test_auth_vk_mobile_saves_attribution(
        self,
        api_client: TestClient,
        third_user: User,
        db: Session,
    ):
        """Новый юзер + attribution → фиксируем реферера и канал (фича 0003)."""
        response = api_client.post(
            '/auth/vk/mobile',
            json={
                'access_token': 'attr_token',
                'email': 'attr_vk@test.com',
                'phone': None,
                'attribution': {
                    'referrer_id': str(third_user.id),
                    'utm_source': 'telegram',
                },
            },
        )
        assert response.is_success
        user = db.scalars(
            select(User).where(User.vk_access_token == 'attr_token')
        ).one()
        row = db.scalars(
            select(UserAttribution).where(UserAttribution.user_id == user.id)
        ).one()
        assert row.referrer_id == third_user.id
        assert row.utm_source == 'telegram'

    def test_auth_vk_web_passes_attribution(
        self,
        api_client: TestClient,
        db: Session,
        mocker,
    ):
        """Веб-эндпоинт прокидывает attribution в auth_vk."""
        mocker.patch(
            'app.routers.auth.exchange_tokens',
            return_value=(
                'web_attr_token',
                VkUserExtraData(email='web_attr@mail.com', phone=None),
            ),
        )
        response = api_client.post(
            '/auth/vk/web',
            json={
                'silent_token': 's',
                'uuid': 'u',
                'attribution': {'utm_source': 'vk'},
            },
        )
        assert response.is_success
        user = db.scalars(
            select(User).where(User.vk_access_token == 'web_attr_token')
        ).one()
        row = db.scalars(
            select(UserAttribution).where(UserAttribution.user_id == user.id)
        ).one()
        assert row.utm_source == 'vk'
        assert row.referrer_id is None

    def test_auth_firebase_saves_attribution(
        self,
        api_client: TestClient,
        other_user: User,
        db: Session,
    ):
        """Firebase-регистрация нового юзера фиксирует attribution."""
        response = api_client.post(
            '/auth/firebase',
            json={
                'id_token': 'id_token',
                'attribution': {
                    'referrer_id': str(other_user.id),
                    'utm_source': 'whatsapp',
                },
            },
        )
        assert response.is_success
        user = db.scalars(select(User).where(User.firebase_uid == 'uid')).one()
        row = db.scalars(
            select(UserAttribution).where(UserAttribution.user_id == user.id)
        ).one()
        assert row.referrer_id == other_user.id
        assert row.utm_source == 'whatsapp'

    def test_auth_firebase_existing_user_ignores_attribution(
        self,
        api_client: TestClient,
        other_user: User,
        db: Session,
    ):
        """Повторный логин с attribution ничего не пишет (first-touch неизменен)."""
        first = api_client.post('/auth/firebase', json={'id_token': 'id_token'})
        assert first.is_success
        second = api_client.post(
            '/auth/firebase',
            json={
                'id_token': 'id_token',
                'attribution': {'referrer_id': str(other_user.id)},
            },
        )
        assert second.is_success
        user = db.scalars(select(User).where(User.firebase_uid == 'uid')).one()
        count = db.scalars(
            select(func.count(UserAttribution.id)).where(
                UserAttribution.user_id == user.id
            )
        ).one()
        assert count == 0

    def test_auth_vk_mobile_unverified_email_no_takeover(
        self,
        api_client: TestClient,
        db: Session,
        mocker,
    ):
        """Легаси-mobile НЕ связывает VK-вход с чужим аккаунтом по email из тела
        (email не подтверждён) — иначе захват аккаунта подстановкой чужого email.
        По неподтверждённому email не матчим; firebase отвергает дубль email при
        создании → 409, а НЕ тихий вход в чужой аккаунт."""
        response = api_client.post('/auth/firebase', json={'id_token': 'id_token'})
        assert response.is_success
        firebase_user = db.scalars(select(User).where(User.firebase_uid == 'uid')).one()
        assert firebase_user.vk_id is None

        # Реальный firebase отверг бы создание юзера с уже занятым email.
        mocker.patch(
            'app.routers.auth.create_firebase_user',
            side_effect=AlreadyExistsError('email exists'),
        )
        response = api_client.post(
            '/auth/vk/mobile',
            json={
                'access_token': 'some_vk_token',
                'email': self.FIREBASE_USER_EMAIL,  # чужой email в теле клиента
                'phone': None,
            },
        )
        assert response.status_code == 409

        # Firebase-аккаунт НЕ захвачен: vk_id не подставлен.
        db.refresh(firebase_user)
        assert firebase_user.vk_id is None

    def test_auth_vk_android_success(
        self,
        api_client: TestClient,
        db: Session,
        mocker,
    ):
        """Confidential Flow: обмен code на сервере, новый юзер заводится."""
        mocker.patch(
            'app.routers.auth.exchange_vk_code',
            return_value=(
                'vk2.a.android_token',
                VkUserExtraData(email='android_vk@test.com', phone=None),
            ),
        )
        response = api_client.post(
            '/auth/vk/android',
            json={
                'code': 'auth_code',
                'code_verifier': 'pkce_verifier',
                'device_id': 'device_1',
                'redirect_uri': 'https://hotelki.pro/vk-auth',
            },
        )
        assert response.is_success, response.json()
        body = response.json()
        assert body['user_created'] is True
        assert body['firebase_token']
        user = db.scalars(select(User).where(User.vk_id == '12345678')).one()
        assert user.email == 'android_vk@test.com'

    def test_auth_vk_android_links_verified_email(
        self,
        api_client: TestClient,
        db: Session,
        mocker,
    ):
        """Android-вход с ПОДТВЕРЖДЁННЫМ (из VK ID) email существующего аккаунта
        связывается с ним — второй аккаунт не плодится. Это безопасно: email от VK,
        не из тела клиента."""
        assert api_client.post(
            '/auth/firebase', json={'id_token': 'id_token'}
        ).is_success
        firebase_user = db.scalars(select(User).where(User.firebase_uid == 'uid')).one()

        mocker.patch(
            'app.routers.auth.exchange_vk_code',
            return_value=(
                'vk2.a.android_token',
                VkUserExtraData(email=self.FIREBASE_USER_EMAIL, phone=None),
            ),
        )
        response = api_client.post(
            '/auth/vk/android',
            json={
                'code': 'auth_code',
                'code_verifier': 'pkce_verifier',
                'device_id': 'device_1',
                'redirect_uri': 'https://hotelki.pro/vk-auth',
            },
        )
        assert response.status_code == 200
        # Связался с существующим аккаунтом: vk_id подставлен, второго нет.
        db.refresh(firebase_user)
        assert firebase_user.vk_id == '12345678'
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
    response = auth_client.get('/users/search', params={'q': 'Other test user'})
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
    db.commit()
    assert not db.scalars(select(Wish).where(Wish.id == wish.id)).one_or_none()


class TestWishPermissions:
    def test_cannot_update_other_user_wish(
        self, auth_client: TestClient, other_user_wish: Wish
    ):
        response = auth_client.put(
            f'/wishes/{other_user_wish.id}',
            json={
                'name': 'Hacked name',
                'description': '',
                'price': None,
                'link': None,
            },
        )
        assert response.status_code == 403

    def test_cannot_delete_other_user_wish(
        self, auth_client: TestClient, other_user_wish: Wish
    ):
        response = auth_client.delete(f'/wishes/{other_user_wish.id}')
        assert response.status_code == 403


class TestReservationEdgeCases:
    def test_cannot_reserve_own_wish(
        self, auth_client: TestClient, wish: Wish, user: User
    ):
        response = auth_client.post(f'/wishes/{wish.id}/reserve')
        assert response.status_code == 403

    def test_cannot_reserve_already_reserved_wish_by_other_user(
        self,
        auth_client: TestClient,
        db: Session,
        other_user_wish: Wish,
        third_user: User,
        user: User,
    ):
        other_user_wish.reserved_by = third_user
        db.add(other_user_wish)
        db.commit()

        response = auth_client.post(f'/wishes/{other_user_wish.id}/reserve')
        assert response.status_code == 403

    def test_can_reserve_wish_again_if_already_reserved_by_me(
        self, auth_client: TestClient, db: Session, other_user_wish: Wish, user: User
    ):
        other_user_wish.reserved_by = user
        db.add(other_user_wish)
        db.commit()

        response = auth_client.post(f'/wishes/{other_user_wish.id}/reserve')
        assert response.status_code == 200
        db.refresh(other_user_wish)
        assert other_user_wish.reserved_by_id == user.id

    def test_can_cancel_own_reservation(
        self, auth_client: TestClient, db: Session, other_user_wish: Wish, user: User
    ):
        other_user_wish.reserved_by = user
        db.add(other_user_wish)
        db.commit()

        response = auth_client.post(f'/wishes/{other_user_wish.id}/cancel_reservation')
        assert response.is_success
        db.refresh(other_user_wish)
        assert other_user_wish.reserved_by is None


class TestFollowUnfollow:
    def test_follow_user(
        self, auth_client: TestClient, db: Session, user: User, other_user: User
    ):
        response = auth_client.post(f'/follow/{other_user.id}')
        assert response.status_code == 200
        db.refresh(user)
        assert other_user in user.follows

    def test_follow_user_push(
        self, auth_client: TestClient, db: Session, user: User, other_user: User, mocker
    ):
        other_user.firebase_push_token = 'token'
        db.add(other_user)
        db.commit()
        mock_send = mocker.patch('app.helpers.user_helpers.send_push')

        response = auth_client.post(f'/follow/{other_user.id}')
        assert response.status_code == 200
        mock_send.assert_called_once()
        assert mock_send.call_args.kwargs['target_users'] == [other_user]

    def test_unfollow_user(
        self, auth_client: TestClient, db: Session, user: User, other_user: User
    ):
        user.follows.append(other_user)
        db.commit()

        response = auth_client.post(f'/unfollow/{other_user.id}')
        assert response.status_code == 200
        db.refresh(user)
        assert other_user not in user.follows


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
        assert response.status_code == 200, response.json()
        wish_data = response.json()
        assert wish_data['name'] == 'New wish'

        wish_id = UUID(wish_data['id'])
        wish = db.scalars(select(Wish).where(Wish.id == wish_id)).one()
        assert wish.user_id == user.id

    def test_update_wish(self, auth_client: TestClient, db: Session, wish: Wish):
        response = auth_client.put(
            f'/wishes/{wish.id}',
            json={
                'name': 'Updated name',
                'description': 'Updated description',
                'price': 200,
                'link': 'https://updated.com',
            },
        )
        assert response.status_code == 200
        db.refresh(wish)
        assert wish.name == 'Updated name'

    def test_delete_wish(self, auth_client: TestClient, db: Session, wish: Wish):
        wish_id = wish.id
        response = auth_client.delete(f'/wishes/{wish_id}')
        assert response.status_code == 200
        assert not db.scalars(select(Wish).where(Wish.id == wish_id)).one_or_none()
        assert db.scalars(select(Wish).where(Wish.id == wish_id)).first() is None

    def test_delete_wish_not_found(self, auth_client: TestClient):
        response = auth_client.delete(f'/wishes/{uuid4()}')
        assert response.status_code == 404


@pytest.fixture
def mocked_profile_media(tmp_path: Path, mocker) -> Path:
    # Ensure tmp_path is within media root for relative_to() to work
    mocker.patch('app.routers.users.PROFILE_IMAGES_DIR', tmp_path)
    # Mock settings.MEDIA_ROOT to be a parent of tmp_path
    mocker.patch('app.routers.users.settings.MEDIA_ROOT', tmp_path.parent)
    return tmp_path


class TestUserImages:
    def test_upload_profile_image(
        self,
        auth_client: TestClient,
        db: Session,
        user: User,
        mocked_profile_media: Path,
    ):
        # Подсовываем подставной Host — он НЕ должен попасть в photo_url.
        response = auth_client.post(
            '/set_profile_image',
            files={'image': ('profile.jpg', b'fake image content', 'image/jpeg')},
            headers={'host': 'evil.attacker.com'},
        )
        assert response.is_success
        db.refresh(user)
        assert user.photo_path is not None
        # URL строится из доверенного FRONTEND_URL, а не из заголовка Host.
        assert user.photo_url is not None
        assert user.photo_url.startswith(f'{settings.FRONTEND_URL}/media/')
        assert 'evil.attacker.com' not in user.photo_url

    def test_delete_profile_image_real(
        self,
        auth_client: TestClient,
        db: Session,
        user: User,
        mocked_profile_media: Path,
    ):
        file_path = mocked_profile_media / 'profile.jpg'
        file_path.write_bytes(b'fake')
        user.photo_path = str(file_path)
        db.add(user)
        db.commit()

        response = auth_client.post('/delete_profile_image')
        assert response.status_code == 200
        db.refresh(user)
        assert user.photo_path is None
        assert not file_path.exists()

    def test_auth_vk_web_success(self, api_client: TestClient, mocker):
        mocker.patch(
            'app.routers.auth.exchange_tokens',
            return_value=('token', VkUserExtraData(email='web@mail.com', phone=None)),
        )
        mocker.patch(
            'app.routers.auth.get_vk_user_data_by_access_token',
            return_value=VkUserBasicData(
                id=999,
                first_name='A',
                last_name='B',
                photo_url='',
                gender=Gender.male,
                birthdate=date(2000, 1, 1),
            ),
        )
        mocker.patch('app.routers.auth.create_firebase_user', return_value='uid')
        mocker.patch('app.routers.auth.get_vk_user_friends', return_value=[])
        mocker.patch(
            'app.routers.auth.create_custom_firebase_token', return_value='fb_token'
        )

        response = api_client.post(
            '/auth/vk/web', json={'silent_token': 's', 'uuid': 'u'}
        )
        assert response.status_code == 200
        assert response.json()['vk_access_token'] == 'token'


class TestUsersExtra:
    def test_get_user_success(self, auth_client: TestClient, other_user: User):
        response = auth_client.get(f'/users/{other_user.id}')
        assert response.status_code == 200
        assert response.json()['id'] == str(other_user.id)

    def test_get_user_not_found(self, auth_client: TestClient):
        response = auth_client.get(f'/users/{uuid4()}')
        assert response.status_code == 404

    def test_follow_already_following(
        self, auth_client: TestClient, db: Session, user: User, other_user: User
    ):
        if other_user not in user.follows:
            user.follows.append(other_user)
            db.commit()
        response = auth_client.post(f'/follow/{other_user.id}')
        assert response.status_code == 200

    def test_unfollow_not_following(self, auth_client: TestClient, other_user: User):
        response = auth_client.post(f'/unfollow/{other_user.id}')
        assert response.status_code == 200

    def test_possible_friends_empty(self, auth_client: TestClient):
        response = auth_client.get('/possible_friends')
        assert response.json() == []

    def test_possible_friends_success(
        self, auth_client: TestClient, db: Session, user: User, other_user: User
    ):
        user.vk_friends_data = [{'id': other_user.vk_id}]
        db.add(user)
        db.commit()
        response = auth_client.get('/possible_friends')
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]['id'] == str(other_user.id)

    def test_item_info_parse_error(self, auth_client: TestClient, mocker):
        from app.parsers import ItemInfoParseError

        mocker.patch(
            'app.routers.users.try_parse_item_by_link',
            side_effect=ItemInfoParseError('Error'),
        )
        response = auth_client.post(
            '/item_info_from_page', json={'link': 'https://example.com'}
        )
        assert response.status_code == 400

    def test_item_info_retry_html(self, auth_client: TestClient, mocker):
        from app.parsers import ItemInfoParseError

        mocker.patch(
            'app.routers.users.try_parse_item_by_link',
            side_effect=[
                ItemInfoParseError('fail'),
                {
                    'title': 'retry',
                    'description': 'desc',
                    'image_url': 'http://img.com',
                },
            ],
        )

        response = auth_client.post(
            '/item_info_from_page',
            json={'link': 'https://example.com', 'html': 'some html'},
        )
        assert response.status_code == 200
        assert response.json()['title'] == 'retry'

    def test_item_info_retry_fail(self, auth_client: TestClient, mocker):
        from app.parsers import ItemInfoParseError

        mocker.patch(
            'app.routers.users.try_parse_item_by_link',
            side_effect=[ItemInfoParseError('fail'), ItemInfoParseError('fail again')],
        )
        response = auth_client.post(
            '/item_info_from_page',
            json={'link': 'https://example.com', 'html': 'some html'},
        )
        assert response.status_code == 400

    def test_invite_link(self, auth_client: TestClient):
        response = auth_client.get('/invite_link/')
        assert response.status_code == 200
        assert 'userId=' in response.json()

    def test_users_test_api(self, auth_client: TestClient, mocker):
        mocker.patch('app.routers.users.settings.IS_DEBUG', True)
        response = auth_client.get('/users/')
        assert response.status_code == 200

    def test_users_test_api_not_debug(self, auth_client: TestClient, mocker):
        mocker.patch('app.routers.users.settings.IS_DEBUG', False)
        response = auth_client.get('/users/')
        assert response.status_code == 404

    def test_search_users_empty_query(self, auth_client: TestClient):
        response = auth_client.get('/users/search', params={'q': '  '})
        assert response.json() == []

    def test_user_followers(
        self, auth_client: TestClient, user: User, other_user: User, db: Session
    ):
        other_user.follows.append(user)
        db.commit()
        response = auth_client.get(f'/users/{user.id}/followers')
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_users_followed_by_this_user(
        self, auth_client: TestClient, user: User, other_user: User, db: Session
    ):
        user.follows.append(other_user)
        db.commit()
        response = auth_client.get(f'/users/{user.id}/follows')
        assert response.status_code == 200
        assert len(response.json()) == 1


class TestWishesExtra:
    def test_get_wish_archived_other_user(
        self, auth_client: TestClient, other_user_wish: Wish, db: Session
    ):
        other_user_wish.is_archived = True
        db.commit()
        response = auth_client.get(f'/wishes/{other_user_wish.id}')
        assert response.status_code == 404

    def test_upload_wish_image(
        self, auth_client: TestClient, wish: Wish, db: Session, tmp_path: Path, mocker
    ):
        mocker.patch('app.routers.wishes.WISH_IMAGES_DIR', tmp_path)
        response = auth_client.post(
            f'/wishes/{wish.id}/image',
            files={'file': ('image.jpg', b'fake content', 'image/jpeg')},
        )
        assert response.status_code == 200
        db.refresh(wish)
        assert wish.image is not None
        assert (tmp_path / wish.image).exists()

    def test_delete_wish_image(self, auth_client: TestClient, wish: Wish, db: Session):
        wish.image = 'fake.jpg'
        db.commit()
        response = auth_client.delete(f'/wishes/{wish.id}/image')
        assert response.status_code == 200
        db.refresh(wish)
        assert wish.image is None

    def test_user_wishes_not_found(self, auth_client: TestClient):
        response = auth_client.get(f'/users/{uuid4()}/wishes')
        assert response.status_code == 404

    def test_reserve_wish_not_found(self, auth_client: TestClient):
        response = auth_client.post(f'/wishes/{uuid4()}/reserve')
        assert response.status_code == 404

    def test_cancel_wish_reservation_not_found(self, auth_client: TestClient):
        response = auth_client.post(f'/wishes/{uuid4()}/cancel_reservation')
        assert response.status_code == 404

    def test_cancel_wish_reservation_by_other_user(
        self,
        auth_client: TestClient,
        other_user_wish: Wish,
        third_user: User,
        db: Session,
    ):
        other_user_wish.reserved_by = third_user
        db.commit()
        response = auth_client.post(f'/wishes/{other_user_wish.id}/cancel_reservation')
        assert response.status_code == 403


class TestRecommendations:
    @pytest.fixture
    def recommendation(self, db: Session):
        rec = WishRecommendation(
            title='Recommended item',
            description='A great thing',
            price=500,
            link='https://partner-shop.com/item',
            image_url='https://partner-shop.com/img.jpg',
        )
        db.add(rec)
        db.commit()
        return rec

    def test_list_recommendations_empty(self, auth_client: TestClient):
        response = auth_client.get('/wish_recommendations')
        assert response.status_code == 200
        assert response.json() == {
            'items': [],
            'total': 0,
            'has_next': False,
            'has_previous': False,
        }

    def test_list_recommendations(
        self, auth_client: TestClient, recommendation: WishRecommendation
    ):
        response = auth_client.get('/wish_recommendations')
        assert response.status_code == 200
        data = response.json()
        assert data['total'] == 1
        assert data['has_next'] is False
        assert data['has_previous'] is False
        assert len(data['items']) == 1
        assert data['items'][0]['title'] == 'Recommended item'
        assert data['items'][0]['link'] == 'https://partner-shop.com/item'

    def test_list_recommendations_pagination(
        self, auth_client: TestClient, db: Session
    ):
        for i in range(5):
            db.add(
                WishRecommendation(
                    title=f'Item {i}',
                    link=f'https://partner-shop.com/item/{i}',
                )
            )
        db.commit()

        # Первая страница: есть следующая, нет предыдущей.
        response = auth_client.get('/wish_recommendations?limit=2&offset=0')
        assert response.status_code == 200
        data = response.json()
        assert data['total'] == 5
        assert len(data['items']) == 2
        assert data['has_next'] is True
        assert data['has_previous'] is False

        # Последняя страница: нет следующей, есть предыдущая.
        response = auth_client.get('/wish_recommendations?limit=2&offset=4')
        assert response.status_code == 200
        data = response.json()
        assert len(data['items']) == 1
        assert data['has_next'] is False
        assert data['has_previous'] is True

    def test_list_recommendations_invalid_pagination(self, auth_client: TestClient):
        response = auth_client.get('/wish_recommendations?limit=0')
        assert response.status_code == 422

    def test_get_recommendation(
        self, auth_client: TestClient, recommendation: WishRecommendation
    ):
        response = auth_client.get(f'/wish_recommendations/{recommendation.id}')
        assert response.status_code == 200
        assert response.json()['title'] == 'Recommended item'
        assert response.json()['wishes_count'] == 0

    def test_get_recommendation_not_found(self, auth_client: TestClient):
        response = auth_client.get(f'/wish_recommendations/{uuid4()}')
        assert response.status_code == 404

    def test_create_wish_with_recommendation(
        self,
        auth_client: TestClient,
        db: Session,
        recommendation: WishRecommendation,
        user: User,
    ):
        response = auth_client.post(
            '/wishes',
            json={
                'name': 'Rec wish',
                'description': 'From recommendation',
                'price': 500,
                'link': recommendation.link,
                'recommendation_id': str(recommendation.id),
            },
        )
        assert response.status_code == 200
        wish_data = response.json()
        assert wish_data['recommendation_id'] == str(recommendation.id)

        wish = db.scalars(select(Wish).where(Wish.id == wish_data['id'])).one()
        assert wish.recommendation_id == recommendation.id

    def test_create_wish_with_nonexistent_recommendation(self, auth_client: TestClient):
        response = auth_client.post(
            '/wishes',
            json={
                'name': 'Bad wish',
                'description': None,
                'price': None,
                'link': 'https://example.com',
                'recommendation_id': str(uuid4()),
            },
        )
        assert response.status_code == 404

    def test_create_wish_without_recommendation(
        self, auth_client: TestClient, db: Session, user: User
    ):
        response = auth_client.post(
            '/wishes',
            json={
                'name': 'Regular wish',
                'description': None,
                'price': None,
                'link': None,
            },
        )
        assert response.status_code == 200
        wish_data = response.json()
        assert wish_data['recommendation_id'] is None

    def test_recommendation_wishes_count(
        self,
        auth_client: TestClient,
        db: Session,
        recommendation: WishRecommendation,
        user: User,
    ):
        for i in range(3):
            wish = Wish(
                user_id=user.id,
                name=f'Wish {i}',
                recommendation_id=recommendation.id,
            )
            db.add(wish)
        db.commit()

        response = auth_client.get(f'/wish_recommendations/{recommendation.id}')
        assert response.status_code == 200
        assert response.json()['wishes_count'] == 3
