from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import Response

from app.constants import ACTIVITY_STATE_RADAR_OPENED, ACTIVITY_STATE_USER_ID
from app.db import User, UserActivityDay
from app.helpers.activity import (
    ROUTE_HEADER,
    USER_ID_HEADER,
    record_activity,
    record_request_activity,
    set_activity_headers,
)
from app.main import app
from app.test_auth import build_test_token
from app.utils import utc_now


@pytest.fixture
def user(db: Session) -> User:
    user = User(
        display_name='Активный',
        firebase_uid='activity_uid',
        registered_at=utc_now(),
    )
    db.add(user)
    db.commit()
    return user


def _rows(db: Session, user: User) -> list[UserActivityDay]:
    return list(
        db.scalars(
            select(UserActivityDay)
            .where(UserActivityDay.user_id == user.id)
            .order_by(UserActivityDay.activity_date)
        )
    )


def _make_request(scope_extra: dict | None = None) -> Request:
    return Request(scope={'type': 'http', 'headers': [], **(scope_extra or {})})


def test_record_activity_creates_row(db: Session, user: User):
    now = utc_now()
    record_activity(user.id, now=now)

    (row,) = _rows(db, user)
    assert row.activity_date == now.date()
    assert row.request_count == 1
    assert row.radar_open_count == 0
    assert row.first_seen_at == row.last_seen_at


def test_record_activity_same_day_upserts(db: Session, user: User):
    first = utc_now()
    later = first + timedelta(hours=2)
    record_activity(user.id, now=first)
    record_activity(user.id, now=later)

    # Сутки те же — строка одна: рост таблицы ограничен ключом (user_id, date).
    (row,) = _rows(db, user)
    assert row.request_count == 2
    # first_seen_at — про первый заход в сутки, его upsert не трогает.
    assert row.first_seen_at == first
    assert row.last_seen_at == later


def test_record_activity_counts_radar_opens(db: Session, user: User):
    now = utc_now()
    record_activity(user.id, now=now)
    record_activity(user.id, radar_opens=1, now=now)
    record_activity(user.id, radar_opens=1, now=now)

    (row,) = _rows(db, user)
    assert row.request_count == 3
    assert row.radar_open_count == 2


def test_record_activity_new_day_new_row(db: Session, user: User):
    today = utc_now()
    record_activity(user.id, now=today)
    record_activity(user.id, now=today + timedelta(days=1))

    rows = _rows(db, user)
    assert len(rows) == 2
    assert [row.request_count for row in rows] == [1, 1]


def test_record_request_activity_without_user_writes_nothing(db: Session, user: User):
    # Неавторизованные запросы (публичный вишлист, OG, health) следа не оставляют.
    record_request_activity(_make_request())

    assert _rows(db, user) == []


def test_record_request_activity_marks_radar(db: Session, user: User):
    request = _make_request()
    setattr(request.state, ACTIVITY_STATE_USER_ID, user.id)
    setattr(request.state, ACTIVITY_STATE_RADAR_OPENED, True)

    record_request_activity(request)

    (row,) = _rows(db, user)
    assert row.request_count == 1
    assert row.radar_open_count == 1


def test_record_request_activity_swallows_errors(db: Session, user: User):
    # Прибор не имеет права ломать запрос: ссылка на несуществующего юзера
    # роняет FK, но наружу исключение не выходит.
    request = _make_request()
    setattr(request.state, ACTIVITY_STATE_USER_ID, uuid4())

    record_request_activity(request)

    assert _rows(db, user) == []


def test_set_activity_headers_without_user_or_route():
    response = Response()
    set_activity_headers(_make_request(), response)

    assert USER_ID_HEADER not in response.headers
    assert ROUTE_HEADER not in response.headers


def test_set_activity_headers_sets_marks(user: User):
    # Шаблон роута, а не сырой путь: иначе каждый UUID в пути даёт свой бакет.
    route = next(r for r in app.router.routes if getattr(r, 'path', None))
    request = _make_request({'route': route})
    setattr(request.state, ACTIVITY_STATE_USER_ID, user.id)
    response = Response()

    set_activity_headers(request, response)

    assert response.headers[USER_ID_HEADER] == str(user.id)
    assert response.headers[ROUTE_HEADER] == request.scope['route'].path


def test_middleware_records_radar_open_end_to_end(
    db: Session, user: User, test_auth_secret: str
):
    """Сквозной путь: get_current_user → роут → мидлварь после ответа.

    Проверяет и то, что метка доезжает через `request.state` сквозь
    BaseHTTPMiddleware, и то, что заголовки для nginx проставлены.
    """
    # Вход через dev/test-байпас (фича 0009) — он резолвит только сид-юзеров.
    # Секрет включает фикстура `test_auth_secret`, а сам токен собираем продовым
    # `build_test_token`: формат тогда живёт в одном месте, и тест не разъедется
    # с `get_current_user`, если формат поменяется.
    user.is_test = True
    db.commit()
    client = TestClient(app, headers={'Authorization': build_test_token(user)})

    response = client.get('/birthday_radar')

    assert response.status_code == 200
    assert response.headers[USER_ID_HEADER] == str(user.id)
    assert response.headers[ROUTE_HEADER] == '/birthday_radar'
    (row,) = _rows(db, user)
    assert row.request_count == 1
    assert row.radar_open_count == 1


def test_middleware_ignores_unauthenticated(db: Session, user: User):
    client = TestClient(app)

    response = client.get('/health')

    assert response.status_code == 200
    assert USER_ID_HEADER not in response.headers
    assert response.headers[ROUTE_HEADER] == '/health'
    assert _rows(db, user) == []
