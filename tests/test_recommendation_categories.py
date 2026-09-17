"""Рекомендации по категориям (фича 0015): категории под юзера, фильтр
списка, копирование картинки в хотелку. Старый вызов без фильтра не изменился.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import Gender, RecommendationCategory
from app.db import User, Wish, WishRecommendation
from app.dependencies import get_current_user, get_db
from app.helpers.recommendations import copy_recommendation_image
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


def test_categories_empty_when_no_content(auth_client: TestClient):
    response = auth_client.get(f'{URL}/categories')
    assert response.status_code == 200
    assert response.json() == {'items': []}


@pytest.mark.usefixtures('two_categories')
def test_categories_default_order_without_gender(auth_client: TestClient):
    # Пола нет → дефолтный порядок; только категории с товарами, с заголовками.
    assert auth_client.get(f'{URL}/categories').json() == {
        'items': [
            {'code': 'jewelry', 'title': 'Украшения'},
            {'code': 'hobby', 'title': 'Игры и хобби'},
        ]
    }


@pytest.mark.usefixtures('two_categories')
def test_categories_gender_only_reorders(
    auth_client: TestClient, db: Session, user: User
):
    # Мужчине первыми идут его категории (hobby), остальные — в дефолтном порядке;
    # набор тот же: таргетинг меняет только порядок.
    user.gender = Gender.male
    db.commit()
    codes = [c['code'] for c in auth_client.get(f'{URL}/categories').json()['items']]
    assert codes == ['hobby', 'jewelry']
    user.gender = Gender.female
    db.commit()
    codes = [c['code'] for c in auth_client.get(f'{URL}/categories').json()['items']]
    assert codes == ['jewelry', 'hobby']


class TestCopyImage:
    def test_copies_file_and_returns_name(self, tmp_path: Path):
        source_dir = tmp_path / 'recommendation_images'
        source_dir.mkdir()
        (source_dir / 'ab.jpg').write_bytes(b'img')
        wish_dir = tmp_path / 'wish_images'
        name = copy_recommendation_image(
            '/media/recommendation_images/ab.jpg', tmp_path, wish_dir
        )
        assert name == 'ab.jpg'
        assert (wish_dir / 'ab.jpg').read_bytes() == b'img'

    def test_missing_file_is_none(self, tmp_path: Path):
        assert (
            copy_recommendation_image(
                '/media/recommendation_images/gone.jpg', tmp_path, tmp_path / 'w'
            )
            is None
        )

    def test_foreign_or_empty_url_is_none(self, tmp_path: Path):
        assert copy_recommendation_image(None, tmp_path, tmp_path) is None
        assert copy_recommendation_image('https://x/y.jpg', tmp_path, tmp_path) is None


def test_create_wish_from_recommendation_copies_image(
    auth_client: TestClient, db: Session, user: User, tmp_path: Path, mocker
):
    source_dir = tmp_path / 'recommendation_images'
    source_dir.mkdir()
    (source_dir / 'cd.webp').write_bytes(b'webp')
    mocker.patch('app.routers.wishes.settings.MEDIA_ROOT', tmp_path)
    mocker.patch('app.routers.wishes.WISH_IMAGES_DIR', tmp_path / 'wish_images')
    rec = WishRecommendation(
        title='Alias',
        link='https://example.com/alias',
        image_url='/media/recommendation_images/cd.webp',
        category=RecommendationCategory.hobby,
    )
    db.add(rec)
    db.commit()
    response = auth_client.post(
        '/wishes',
        json={
            'name': 'Alias',
            'description': None,
            'price': None,
            'link': rec.link,
            'recommendation_id': str(rec.id),
        },
    )
    assert response.status_code == 200
    assert response.json()['image'] == '/media/wish_images/cd.webp'
    wish = db.scalars(select(Wish).where(Wish.recommendation_id == rec.id)).one()
    assert wish.image == 'cd.webp'
    assert (tmp_path / 'wish_images' / 'cd.webp').read_bytes() == b'webp'


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
