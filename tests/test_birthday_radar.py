from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.constants import Gender
from app.db import User, Wish
from app.main import app, get_current_user, get_db
from app.routers.birthday_radar import (
    days_until_birthday,
    parse_vk_bdate_day_month,
)
from app.utils import utc_now

TODAY = date.today()


def _bdate_in(days: int) -> str:
    """VK-строка `D.M` для дня рождения через `days` дней от сегодня."""
    target = TODAY + timedelta(days=days)
    return f'{target.day}.{target.month}'


def _date_in(days: int) -> date:
    """Полная дата рождения (год 1990) с днём/месяцем через `days` дней."""
    target = TODAY + timedelta(days=days)
    return date(1990, target.month, target.day)


def _make_user(db: Session, suffix: str, **kwargs) -> User:
    user = User(
        display_name=kwargs.pop('display_name', f'User {suffix}'),
        email=f'user{suffix}@mail.ru',
        vk_id=kwargs.pop('vk_id', f'vk_{suffix}'),
        vk_friends_data=kwargs.pop('vk_friends_data', []),
        firebase_uid=f'firebase_{suffix}',
        gender=Gender.female,
        registered_at=utc_now(),
        **kwargs,
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def current_user(db: Session) -> User:
    return _make_user(db, 'me', display_name='Me', vk_id='vk_me')


@pytest.fixture(autouse=True)
def _override(current_user: User, db: Session):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: current_user
    yield
    app.dependency_overrides = {}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestParseVkBdate:
    def test_full_date(self):
        assert parse_vk_bdate_day_month('15.3.1990') == (15, 3)

    def test_day_month_only(self):
        # Год скрыт — берём день/месяц (в отличие от app.vk._parse_vk_birthdate).
        assert parse_vk_bdate_day_month('2.8') == (2, 8)

    def test_leap_day_kept(self):
        assert parse_vk_bdate_day_month('29.2') == (29, 2)

    @pytest.mark.parametrize('value', [None, '', '15', '15.13', 'ab.cd', '31.4'])
    def test_invalid_returns_none(self, value):
        assert parse_vk_bdate_day_month(value) is None


class TestDaysUntilBirthday:
    def test_today_is_zero(self):
        assert days_until_birthday(TODAY.day, TODAY.month, TODAY) == 0

    def test_leap_day_in_non_leap_year(self):
        # 29 февраля в невисокосном 2025 отмечаем 28 февраля.
        today = date(2025, 2, 1)
        assert days_until_birthday(29, 2, today) == 27

    def test_next_year_when_passed(self):
        today = date(2025, 12, 31)
        assert days_until_birthday(1, 1, today) == 1


class TestBirthdayRadarEmpty:
    def test_no_vk_no_follows(self, client: TestClient, current_user: User, db):
        current_user.vk_id = None
        db.commit()
        response = client.get('/birthday_radar')
        assert response.is_success
        assert response.json() == {'vk_linked': False, 'entries': []}

    def test_vk_linked_but_no_known_birthdays(
        self, client: TestClient, current_user: User, db
    ):
        # Привязка есть, но у друга нет распознаваемой даты рождения.
        current_user.vk_friends_data = [{'id': 'x', 'bdate': ''}]
        db.commit()
        response = client.get('/birthday_radar')
        assert response.json() == {'vk_linked': True, 'entries': []}


class TestBirthdayRadarInApp:
    def test_vk_friend_in_app_with_wishes(
        self, client: TestClient, current_user: User, db
    ):
        friend = _make_user(db, 'f1', display_name='Аня', vk_id='vk_f1')
        friend.birth_date = _date_in(7)
        db.add_all(
            [
                Wish(user_id=friend.id, name='w1'),
                Wish(user_id=friend.id, name='w2'),
                Wish(user_id=friend.id, name='archived', is_archived=True),
            ]
        )
        current_user.vk_friends_data = [
            {'id': 'vk_f1', 'first_name': 'Аня', 'last_name': 'К', 'bdate': '1.1'}
        ]
        db.commit()

        entries = client.get('/birthday_radar').json()['entries']
        assert len(entries) == 1
        entry = entries[0]
        assert entry['kind'] == 'in_app'
        assert entry['display_name'] == 'Аня'
        assert entry['user_id'] == str(friend.id)
        # ДР берётся из профиля (не из VK-фолбэка 1.1).
        assert entry['days_until_birthday'] == 7
        # Активные хотелки — без архивной.
        assert entry['active_wishes_count'] == 2
        assert entry['followed_by_me'] is False
        assert entry['vk_id'] is None

    def test_in_app_falls_back_to_vk_bdate(
        self, client: TestClient, current_user: User, db
    ):
        # Друг в приложении, но без даты в профиле → берём день/месяц из VK.
        _make_user(db, 'f2', vk_id='vk_f2')
        current_user.vk_friends_data = [{'id': 'vk_f2', 'bdate': _bdate_in(3)}]
        db.commit()

        entry = client.get('/birthday_radar').json()['entries'][0]
        assert entry['kind'] == 'in_app'
        assert entry['days_until_birthday'] == 3
        assert entry['active_wishes_count'] == 0

    def test_followed_user_with_birthday(
        self, client: TestClient, current_user: User, db
    ):
        # Подписка без VK-дружбы: попадает в радар как in_app, followed_by_me=True.
        followed = _make_user(db, 'fol', display_name='Игорь', vk_id='vk_fol')
        followed.birth_date = _date_in(10)
        current_user.follows.append(followed)
        db.commit()

        entry = client.get('/birthday_radar').json()['entries'][0]
        assert entry['display_name'] == 'Игорь'
        assert entry['followed_by_me'] is True
        assert entry['days_until_birthday'] == 10

    def test_followed_user_without_birthday_skipped(
        self, client: TestClient, current_user: User, db
    ):
        followed = _make_user(db, 'nob', vk_id='vk_nob')  # нет birth_date
        current_user.follows.append(followed)
        db.commit()
        assert client.get('/birthday_radar').json()['entries'] == []

    def test_dedup_vk_friend_also_followed(
        self, client: TestClient, current_user: User, db
    ):
        friend = _make_user(db, 'dup', display_name='Оля', vk_id='vk_dup')
        friend.birth_date = _date_in(5)
        current_user.follows.append(friend)
        current_user.vk_friends_data = [{'id': 'vk_dup', 'bdate': '1.1'}]
        db.commit()

        entries = client.get('/birthday_radar').json()['entries']
        assert len(entries) == 1
        assert entries[0]['followed_by_me'] is True

    def test_self_excluded(self, client: TestClient, current_user: User, db):
        # Собственный vk_id среди друзей не должен добавить себя в радар.
        current_user.birth_date = _date_in(2)
        current_user.vk_friends_data = [{'id': 'vk_me', 'bdate': '1.1'}]
        db.commit()
        assert client.get('/birthday_radar').json()['entries'] == []


class TestBirthdayRadarInvite:
    def test_vk_friend_not_in_app(self, client: TestClient, current_user: User, db):
        current_user.vk_friends_data = [
            {
                'id': 987654321,
                'first_name': 'Пётр',
                'last_name': 'Смирнов',
                'bdate': _bdate_in(4),
            }
        ]
        db.commit()

        entry = client.get('/birthday_radar').json()['entries'][0]
        assert entry['kind'] == 'invite'
        assert entry['display_name'] == 'Пётр Смирнов'
        assert entry['vk_id'] == '987654321'
        assert entry['user_id'] is None
        assert entry['active_wishes_count'] is None
        assert entry['followed_by_me'] is None
        assert entry['photo_url'] is None
        assert entry['days_until_birthday'] == 4

    def test_invite_nameless_friend_fallback(
        self, client: TestClient, current_user: User, db
    ):
        current_user.vk_friends_data = [{'id': 111, 'bdate': _bdate_in(6)}]
        db.commit()
        entry = client.get('/birthday_radar').json()['entries'][0]
        assert entry['display_name'] == 'Друг из ВК'

    def test_friend_without_id_skipped(
        self, client: TestClient, current_user: User, db
    ):
        current_user.vk_friends_data = [{'bdate': _bdate_in(1)}]  # нет id
        db.commit()
        assert client.get('/birthday_radar').json()['entries'] == []


class TestBirthdayRadarSorting:
    def test_sorted_by_days_until(self, client: TestClient, current_user: User, db):
        current_user.vk_friends_data = [
            {'id': 1, 'first_name': 'Дальний', 'bdate': _bdate_in(20)},
            {'id': 2, 'first_name': 'Ближний', 'bdate': _bdate_in(2)},
            {'id': 3, 'first_name': 'Средний', 'bdate': _bdate_in(9)},
        ]
        db.commit()
        days = [
            e['days_until_birthday']
            for e in client.get('/birthday_radar').json()['entries']
        ]
        assert days == [2, 9, 20]
