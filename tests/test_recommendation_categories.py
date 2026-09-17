"""Рекомендации по категориям (фича 0015) — пока только контракт.

Ручка категорий — заглушка `501` до заморозки (PROTOCOL.md §5). Тесты
фиксируют форму: авторизация обязательна, фильтр `category` валидируется и
работает, старый вызов без фильтра не изменился.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.constants import RecommendationCategory
from app.db import User, WishRecommendation
from app.dependencies import get_current_user, get_db
from app.main import app
from app.utils import utc_now

URL = '/wish_recommendations'


@pytest.fixture
def user(db: Session) -> User:
    user = User(
        display_name='Автор',
        email='author@test.ru',
        firebase_uid='author-uid',
        registered_at=utc_now(),
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture(autouse=True)
def _db_override(db: Session) -> Iterator[None]:
    app.dependency_overrides[get_db] = lambda: db
    yield
    app.dependency_overrides = {}


@pytest.fixture
def auth_client(user: User) -> Iterator[TestClient]:
    # Авторизация подменена; тесты на 401 её не подменяют и идут без заголовка.
    app.dependency_overrides[get_current_user] = lambda: user
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user)


@pytest.fixture
def two_categories(db: Session) -> None:
    db.add_all(
        [
            WishRecommendation(
                title='Alias',
                link='https://www.wildberries.ru/catalog/173825315/detail.aspx',
                category=RecommendationCategory.hobby,
            ),
            WishRecommendation(
                title='Серьги',
                link='https://www.wildberries.ru/catalog/149285080/detail.aspx',
                category=RecommendationCategory.jewelry,
            ),
        ]
    )
    db.commit()


def test_categories_require_auth(db: Session):
    assert TestClient(app).get(f'{URL}/categories').status_code == 401


def test_categories_not_implemented_yet(auth_client: TestClient):
    assert auth_client.get(f'{URL}/categories').status_code == 501


def test_list_requires_auth(db: Session):
    assert TestClient(app).get(URL).status_code == 401


def test_list_rejects_unknown_category(auth_client: TestClient):
    response = auth_client.get(URL, params={'category': 'cars'})
    assert response.status_code == 422
    assert response.json()['detail'][0]['loc'] == ['query', 'category']


@pytest.mark.usefixtures('two_categories')
def test_list_filters_by_category(auth_client: TestClient):
    response = auth_client.get(URL, params={'category': 'jewelry'})
    assert response.status_code == 200
    data = response.json()
    assert data['total'] == 1
    assert data['items'][0]['title'] == 'Серьги'
    assert data['items'][0]['category'] == 'jewelry'


@pytest.mark.usefixtures('two_categories')
def test_list_without_category_returns_everything(auth_client: TestClient):
    # Старый клиент без фильтра видит всё, как до 0015.
    assert auth_client.get(URL).json()['total'] == 2


@pytest.mark.usefixtures('two_categories')
def test_list_empty_category(auth_client: TestClient):
    data = auth_client.get(URL, params={'category': 'kids'}).json()
    assert data == {'items': [], 'total': 0, 'has_next': False, 'has_previous': False}
