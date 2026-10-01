"""Гостевой резерв на публичной странице и слияние гостя при входе (фича 0018)."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.constants import GUEST_COOKIE_NAME, HIDDEN_RESERVER_ID, FollowEventSource
from app.db import (
    FollowEvent,
    Guest,
    GuestReservationEvent,
    PushInstallation,
    PushReason,
    PushSendingLog,
    User,
    Wish,
)
from app.guests import delete_orphan_guests
from app.main import app, get_current_user
from app.notifications import (
    send_new_follower_notifications,
    send_reservation_notifincations,
)
from app.utils import utc_now
from app.vk import VkUserBasicData, VkUserExtraData


def _user(db, name: str, **kwargs) -> User:
    user = User(
        display_name=name,
        firebase_uid=f'uid-{name}',
        registered_at=utc_now(),
        **kwargs,
    )
    db.add(user)
    db.commit()
    return user


def _wish(db, owner: User, name: str, **kwargs) -> Wish:
    wish = Wish(user_id=owner.id, name=name, **kwargs)
    db.add(wish)
    db.commit()
    return wish


@pytest.fixture
def guest_client() -> TestClient:
    """Браузер гостя: https — иначе httpx не хранит и не шлёт Secure-куку."""
    return TestClient(app, base_url='https://testserver')


@pytest.fixture
def owner(db) -> User:
    return _user(db, 'Owner')


@pytest.fixture
def auth_like_user_client(db) -> Iterator[TestClient]:
    """Авторизованный юзер приложения (не гость)."""
    viewer = _user(db, 'Viewer')
    app.dependency_overrides[get_current_user] = lambda: viewer
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


def _reserve(client: TestClient, wish: Wish, **kwargs):
    return client.post(
        f'/public/users/{wish.user_id}/wishes/{wish.id}/reserve', **kwargs
    )


def _cancel(client: TestClient, wish: Wish):
    return client.post(
        f'/public/users/{wish.user_id}/wishes/{wish.id}/cancel_reservation'
    )


def _wishlist(client: TestClient, owner: User) -> dict[str, dict]:
    data = client.get(f'/public/users/{owner.id}/wishlist').json()
    return {w['name']: w for w in data['wishes']}


class TestGuestReserve:
    def test_first_reserve_creates_guest_and_cookie(
        self, guest_client: TestClient, db, owner: User
    ):
        wish = _wish(db, owner, 'Кофемолка')

        response = _reserve(
            guest_client, wish, headers={'X-Forwarded-For': '6.6.6.6, 1.2.3.4'}
        )

        assert response.status_code == 200
        assert response.json()['is_reserved'] is True
        assert response.json()['reserved_by_me'] is True
        assert response.headers['Cache-Control'] == 'private, no-store'
        cookie = response.headers['set-cookie']
        for attribute in ('Path=/', 'HttpOnly', 'Secure', 'SameSite=lax'):
            assert attribute in cookie
        assert f'Max-Age={365 * 24 * 60 * 60}' in cookie
        guest = db.scalars(select(Guest)).one()
        assert guest_client.cookies[GUEST_COOKIE_NAME] == guest.token
        db.refresh(wish)
        assert wish.reserved_by_guest_id == guest.id
        assert wish.reserved_at is not None
        # IP — последний адрес X-Forwarded-For: его дописал наш nginx.
        event = db.scalars(select(GuestReservationEvent)).one()
        assert (event.ip, event.guest_id, event.wish_id) == (
            '1.2.3.4',
            guest.id,
            wish.id,
        )

    def test_repeat_is_idempotent_and_same_guest(
        self, guest_client: TestClient, db, owner: User
    ):
        first = _wish(db, owner, 'first')
        second = _wish(db, owner, 'second')
        _reserve(guest_client, first)

        again = _reserve(guest_client, first)
        other = _reserve(guest_client, second)

        assert again.status_code == 200
        assert 'set-cookie' not in again.headers
        assert other.json()['reserved_by_me'] is True
        assert len(db.scalars(select(Guest)).all()) == 1
        # Повтор по своей не пишет второго события (и не тратит лимит IP).
        assert len(db.scalars(select(GuestReservationEvent)).all()) == 2

    def test_reserved_by_other_is_conflict(self, guest_client: TestClient, db, owner):
        giver = _user(db, 'Giver')
        by_user = _wish(db, owner, 'by user', reserved_by_id=giver.id)
        by_guest = _wish(db, owner, 'by guest')
        _reserve(TestClient(app, base_url='https://testserver'), by_guest)

        for wish in (by_user, by_guest):
            response = _reserve(guest_client, wish)
            assert response.status_code == 409
            assert response.json() == {'detail': 'Уже забронировано'}
        # Отказ гостя не создаёт.
        assert len(db.scalars(select(Guest)).all()) == 1
        assert GUEST_COOKIE_NAME not in guest_client.cookies

    def test_owner_not_found(self, guest_client: TestClient, db, owner):
        wish = _wish(db, owner, 'x')
        response = guest_client.post(
            f'/public/users/{uuid4()}/wishes/{wish.id}/reserve'
        )
        assert response.status_code == 404

    def test_archived_or_foreign_wish_is_gone(self, guest_client, db, owner):
        archived = _wish(db, owner, 'archived', is_archived=True)
        stranger = _user(db, 'Stranger')
        foreign = _wish(db, stranger, 'foreign')

        assert _reserve(guest_client, archived).status_code == 410
        response = guest_client.post(
            f'/public/users/{owner.id}/wishes/{foreign.id}/reserve'
        )
        assert response.status_code == 410
        assert response.json() == {'detail': 'Хотелки больше нет'}


class TestGuestLimits:
    def test_per_guest_per_list(self, guest_client: TestClient, db, owner, monkeypatch):
        monkeypatch.setattr(settings, 'GUEST_RESERVE_LIST_MIN', 100)
        wishes = [_wish(db, owner, f'w{i}') for i in range(4)]
        for wish in wishes[:3]:
            assert _reserve(guest_client, wish).status_code == 200

        response = _reserve(guest_client, wishes[3])

        assert response.status_code == 429
        assert response.json() == {'detail': 'Сейчас забронировать нельзя'}
        db.refresh(wishes[3])
        assert not wishes[3].is_reserved

    def test_guests_share_of_list(self, db, owner):
        # 8 активных → гости держат не больше 4; каждый гость — свой браузер.
        wishes = [_wish(db, owner, f'w{i}') for i in range(8)]
        for wish in wishes[:4]:
            client = TestClient(app, base_url='https://testserver')
            assert _reserve(client, wish).status_code == 200

        fresh = TestClient(app, base_url='https://testserver')
        response = _reserve(fresh, wishes[4])

        assert response.status_code == 429
        assert GUEST_COOKIE_NAME not in fresh.cookies

    def test_short_list_floor(self, db, owner):
        # 2 хотелки: половина — 1, но пол 3 позволяет гостям занять обе.
        wishes = [_wish(db, owner, f'w{i}') for i in range(2)]
        for wish in wishes:
            client = TestClient(app, base_url='https://testserver')
            assert _reserve(client, wish).status_code == 200

    def test_per_ip_per_minute(self, db, owner, monkeypatch):
        monkeypatch.setattr(settings, 'GUEST_RESERVE_PER_IP_PER_MINUTE', 2)
        wishes = [_wish(db, owner, f'w{i}') for i in range(8)]
        ip = {'X-Forwarded-For': '9.9.9.9'}
        for wish in wishes[:2]:
            client = TestClient(app, base_url='https://testserver')
            assert _reserve(client, wish, headers=ip).status_code == 200
        client = TestClient(app, base_url='https://testserver')
        assert _reserve(client, wishes[2], headers=ip).status_code == 429
        # Другой IP не упирается.
        other = {'X-Forwarded-For': '8.8.8.8'}
        assert _reserve(client, wishes[2], headers=other).status_code == 200
        # Минута прошла — лимит снова свободен.
        for event in db.scalars(select(GuestReservationEvent)):
            event.created_at = utc_now() - timedelta(minutes=2)
        db.commit()
        assert _reserve(client, wishes[3], headers=ip).status_code == 200

    def test_registered_users_not_limited(self, auth_like_user_client, db, owner):
        wishes = [_wish(db, owner, f'w{i}') for i in range(5)]
        for wish in wishes:
            assert auth_like_user_client.post(f'/wishes/{wish.id}/reserve').is_success


class TestGuestCancel:
    def test_cancel_own(self, guest_client: TestClient, db, owner):
        wish = _wish(db, owner, 'x')
        _reserve(guest_client, wish)

        response = _cancel(guest_client, wish)

        assert response.status_code == 200
        assert response.json()['is_reserved'] is False
        assert response.json()['reserved_by_me'] is False
        db.refresh(wish)
        assert wish.reserved_by_guest_id is None
        assert wish.reserved_at is None
        # Снятие свободной — повтор идемпотентен.
        assert _cancel(guest_client, wish).status_code == 200

    def test_cancel_not_yours(self, guest_client: TestClient, db, owner):
        giver = _user(db, 'Giver')
        by_user = _wish(db, owner, 'by user', reserved_by_id=giver.id)
        by_guest = _wish(db, owner, 'by guest')
        _reserve(TestClient(app, base_url='https://testserver'), by_guest)

        for wish in (by_user, by_guest):
            response = _cancel(guest_client, wish)
            assert response.status_code == 403
            assert response.json() == {'detail': 'Это не ваш резерв'}

    def test_cancel_gone_and_missing_owner(self, guest_client: TestClient, db, owner):
        archived = _wish(db, owner, 'archived', is_archived=True)
        assert _cancel(guest_client, archived).status_code == 410
        response = guest_client.post(
            f'/public/users/{uuid4()}/wishes/{archived.id}/cancel_reservation'
        )
        assert response.status_code == 404


class TestReservedByMe:
    def test_wishlist_marks_only_this_guest(self, guest_client, db, owner):
        mine = _wish(db, owner, 'mine')
        theirs = _wish(db, owner, 'theirs')
        free = _wish(db, owner, 'free')
        _reserve(guest_client, mine)
        _reserve(TestClient(app, base_url='https://testserver'), theirs)

        cards = _wishlist(guest_client, owner)

        assert cards['mine']['reserved_by_me'] is True
        assert cards['theirs'] == {**cards['theirs'], 'is_reserved': True}
        assert cards['theirs']['reserved_by_me'] is False
        assert cards['free']['is_reserved'] is False
        assert free.id  # свободная — без флагов
        # Без куки (другой браузер) своих нет.
        stranger = TestClient(app, base_url='https://testserver')
        assert not any(c['reserved_by_me'] for c in _wishlist(stranger, owner).values())

    def test_archive_keeps_guest_reservation(self, guest_client, db, owner):
        wish = _wish(db, owner, 'x')
        _reserve(guest_client, wish)
        wish.is_archived = True
        db.commit()
        assert 'x' not in _wishlist(guest_client, owner)

        wish.is_archived = False
        db.commit()

        assert _wishlist(guest_client, owner)['x']['reserved_by_me'] is True


class TestAuthorizedViewOfGuestReservation:
    def test_hidden_reserver_and_legacy_codes(
        self, guest_client, auth_like_user_client, db, owner
    ):
        wish = _wish(db, owner, 'x')
        _reserve(guest_client, wish)

        read = auth_like_user_client.get(f'/users/{owner.id}/wishes').json()

        assert read[0]['is_reserved'] is True
        assert read[0]['reserved_by_id'] == str(HIDDEN_RESERVER_ID)
        assert (
            auth_like_user_client.post(f'/wishes/{wish.id}/reserve').status_code == 409
        )
        cancel = auth_like_user_client.post(f'/wishes/{wish.id}/cancel_reservation')
        assert cancel.status_code == 403


def test_reservation_push_for_guest_reservation(guest_client, db, owner, fcm):
    owner.push_installations = [PushInstallation(push_token='token-owner')]
    db.commit()
    wish = _wish(db, owner, 'x')
    _reserve(guest_client, wish)

    send_reservation_notifincations()

    (message,) = fcm.messages
    assert message.android.notification.title == 'Кто-то хочет сделать Вам подарок!'
    assert message.data['type'] == 'reservation'
    log = db.scalars(select(PushSendingLog)).one()
    assert log.reason == PushReason.RESERVATION
    assert message.data['delivery_id'] == str(log.id)


@dataclass
class _FirebaseUser:
    email_verified: bool
    email: str
    display_name: str
    photo_url: str | None
    phone_number: str | None


@pytest.fixture
def firebase_login(mocker):
    """Внешняя граница Firebase: токен всегда валиден, профиль — «Даритель»."""
    mocker.patch('app.routers.auth.verify_id_token', return_value={'uid': 'giver-uid'})
    mocker.patch(
        'app.routers.auth.get_firebase_user_data',
        return_value=_FirebaseUser(True, 'giver@test.com', 'Даритель', None, None),
    )


def _login(client: TestClient):
    return client.post('/auth/firebase', json={'id_token': 'token'})


class TestMerge:
    def test_merge_moves_reservations_and_follows_owners(
        self, guest_client, db, owner, firebase_login, fcm
    ):
        second_owner = _user(db, 'Second')
        first = _wish(db, second_owner, 'first')
        other = _wish(db, owner, 'other')
        archived = _wish(db, owner, 'archived')
        for wish in (first, other, archived):
            _reserve(guest_client, wish)
        archived.is_archived = True
        db.commit()

        response = _login(guest_client)

        assert response.status_code == 200
        body = response.json()
        assert body['guest_merged_reservations'] == 3
        # Порядок — по первому резерву гостя в списке владельца.
        assert body['guest_followed_owner_ids'] == [str(second_owner.id), str(owner.id)]
        assert 'Max-Age=0' in response.headers['set-cookie']
        assert GUEST_COOKIE_NAME not in guest_client.cookies
        giver = db.scalars(select(User).where(User.firebase_uid == 'giver-uid')).one()
        for wish in (first, other, archived):
            db.refresh(wish)
            assert (wish.reserved_by_id, wish.reserved_by_guest_id) == (giver.id, None)
        guest = db.scalars(select(Guest)).one()
        assert guest.merged_user_id == giver.id
        assert guest.merged_at is not None
        events = db.scalars(select(FollowEvent)).all()
        assert {e.source for e in events} == {FollowEventSource.guest_reservation}
        # Владельцу пуша «новый подписчик» нет — приватность резерва.
        owner.push_installations = [PushInstallation(push_token='token-owner')]
        db.commit()
        send_new_follower_notifications()
        assert fcm.calls == []

    def test_merge_skips_already_followed_and_own_wishes(
        self, guest_client, db, owner, firebase_login
    ):
        giver = _user(db, 'Даритель', email='giver@test.com')
        giver.firebase_uid = 'giver-uid'
        giver.follows.append(owner)
        db.commit()
        followed = _wish(db, owner, 'followed')
        own = _wish(db, giver, 'own')
        _reserve(guest_client, followed)
        _reserve(guest_client, own)

        body = _login(guest_client).json()

        assert body['user_created'] is False
        assert body['guest_merged_reservations'] == 1
        assert body['guest_followed_owner_ids'] == []
        db.refresh(own)
        assert not own.is_reserved

    def test_second_login_and_stale_cookie(
        self, guest_client, db, owner, firebase_login
    ):
        wish = _wish(db, owner, 'x')
        _reserve(guest_client, wish)
        token = guest_client.cookies[GUEST_COOKIE_NAME]
        _login(guest_client)

        # Ответ входа не дошёл: браузер всё ещё шлёт старую куку.
        again = guest_client.post(
            '/auth/firebase',
            json={'id_token': 'token'},
            headers={'Cookie': f'{GUEST_COOKIE_NAME}={token}'},
        )

        assert again.json()['guest_merged_reservations'] == 0
        assert again.json()['guest_followed_owner_ids'] == []
        assert 'Max-Age=0' in again.headers['set-cookie']

    def test_login_without_cookie_keeps_headers_clean(
        self, guest_client, db, firebase_login
    ):
        response = _login(guest_client)
        assert response.json()['guest_merged_reservations'] == 0
        assert 'set-cookie' not in response.headers


def test_merge_via_vk(guest_client, db, owner, mocker):
    wish = _wish(db, owner, 'x')
    _reserve(guest_client, wish)
    mocker.patch(
        'app.routers.auth.exchange_vk_code',
        return_value=('vk-token', VkUserExtraData(email='vk@test.com', phone=None)),
    )
    mocker.patch(
        'app.routers.auth.get_vk_user_data_by_access_token',
        return_value=VkUserBasicData(
            id=777,
            first_name='Вк',
            last_name='Даритель',
            photo_url='',
            birthdate=None,
            gender=None,
        ),
    )
    mocker.patch('app.routers.auth.get_vk_user_friends', return_value=[])
    mocker.patch('app.routers.auth.create_firebase_user', return_value='vk-uid')
    mocker.patch('app.routers.auth.create_custom_firebase_token', return_value='t')

    response = guest_client.post(
        '/auth/vk/vkid',
        json={
            'code': 'c',
            'code_verifier': 'v',
            'device_id': 'd',
            'redirect_uri': 'https://hotelki.pro/',
        },
    )

    assert response.status_code == 200
    assert response.json()['guest_merged_reservations'] == 1
    assert response.json()['guest_followed_owner_ids'] == [str(owner.id)]
    assert 'Max-Age=0' in response.headers['set-cookie']


def test_delete_orphan_guests(db, owner):
    now = utc_now()
    old = now - timedelta(days=2)
    orphan = Guest(token='orphan', created_at=old)
    fresh = Guest(token='fresh', created_at=now)
    holder = Guest(token='holder', created_at=old)
    merged = Guest(token='merged', created_at=old, merged_user_id=owner.id)
    db.add_all([orphan, fresh, holder, merged])
    db.flush()
    _wish(db, owner, 'held', reserved_by_guest_id=holder.id)

    assert delete_orphan_guests(db, now) == 1
    assert delete_orphan_guests(db, now) == 0

    left = {g.token for g in db.scalars(select(Guest))}
    assert left == {'fresh', 'holder', 'merged'}
