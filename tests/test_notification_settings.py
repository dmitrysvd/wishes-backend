"""Настройки уведомлений (фича 0012) — пока только контракт.

Эндпоинты — заглушки `501` до заморозки контракта (PROTOCOL.md §5: логика
пишется после `agreed`). Тесты фиксируют форму: маршруты существуют,
валидация пути/тела срабатывает раньше заглушки.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import User
from app.main import app, get_current_user, get_db
from app.utils import utc_now


@pytest.fixture
def user(db: Session) -> User:
    _user = User(
        display_name='Test user',
        firebase_uid='firebase uid',
        registered_at=utc_now(),
    )
    db.add(_user)
    db.commit()
    return _user


@pytest.fixture
def client(db: Session, user: User) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    yield TestClient(app)
    app.dependency_overrides = {}


def test_read_is_not_implemented_yet(client: TestClient):
    assert client.get('/users/me/notification_settings').status_code == 501


def test_toggle_is_not_implemented_yet(client: TestClient):
    response = client.put(
        '/users/me/notification_settings/friends', json={'enabled': False}
    )
    assert response.status_code == 501


def test_toggle_unknown_group_is_422(client: TestClient):
    # Группа «Цены и наличие» появится с 0013 — до неё это невалидный путь.
    response = client.put(
        '/users/me/notification_settings/prices', json={'enabled': False}
    )
    assert response.status_code == 422


def test_toggle_without_body_is_422(client: TestClient):
    assert client.put('/users/me/notification_settings/friends').status_code == 422
