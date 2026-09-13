"""Открытие по пушу (фича 0013) — пока только контракт.

Ручка — заглушка `501` до заморозки контракта (PROTOCOL.md §5). Тесты
фиксируют форму: маршрут публичный, валидация тела срабатывает раньше заглушки.
"""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app, get_db


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides = {}


def test_push_opened_is_public_and_not_implemented_yet(client: TestClient):
    # Без Authorization: на холодном старте токена у клиента ещё нет.
    response = client.post('/push/opened', json={'delivery_id': str(uuid4())})
    assert response.status_code == 501


def test_push_opened_rejects_non_uuid(client: TestClient):
    assert client.post('/push/opened', json={'delivery_id': 'x'}).status_code == 422
