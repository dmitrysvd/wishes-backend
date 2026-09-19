from collections.abc import Iterator
from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.constants import PriceObservationStatus, PriceSource, TestPersona
from app.db import User, Wish, WishPriceObservation, user_following_table
from app.main import app, get_db
from app.test_auth import build_test_token, get_or_create_test_user
from app.utils import utc_now


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    def override_get_db():
        return db

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides = {}


@pytest.fixture
def _secret(mocker):
    mocker.patch('app.config.settings.TEST_AUTH_SECRET', 'dev-secret')


# --- Эндпоинт POST /dev/test_token ---


def test_test_token_disabled_returns_404(client, mocker):
    # Секрет не сконфигурен (прод) → эндпоинт ведёт себя так, будто его нет.
    mocker.patch('app.config.settings.TEST_AUTH_SECRET', None)
    response = client.post('/dev/test_token', json={'secret': 'x', 'persona': 'rich'})
    assert response.status_code == 404


def test_test_token_wrong_secret_returns_403(client, _secret):
    response = client.post(
        '/dev/test_token', json={'secret': 'wrong', 'persona': 'rich'}
    )
    assert response.status_code == 403


def test_test_token_rich_success(client, _secret, db: Session):
    response = client.post(
        '/dev/test_token', json={'secret': 'dev-secret', 'persona': 'rich'}
    )
    assert response.is_success
    body = response.json()
    assert body['persona'] == 'rich'
    assert body['token'] == f'dev-secret:{body["user_id"]}'

    user = db.get(User, UUID(body['user_id']))
    assert user is not None and user.is_test


def test_test_token_persona_defaults_to_rich(client, _secret):
    # persona опущена → rich.
    response = client.post('/dev/test_token', json={'secret': 'dev-secret'})
    assert response.is_success
    assert response.json()['persona'] == 'rich'


def test_test_token_empty_success(client, _secret):
    response = client.post(
        '/dev/test_token', json={'secret': 'dev-secret', 'persona': 'empty'}
    )
    assert response.is_success
    assert response.json()['persona'] == 'empty'


def test_test_token_idempotent(client, _secret, db: Session):
    # Повторный вызов — тот же юзер, без дублирования сид-данных.
    first = client.post(
        '/dev/test_token', json={'secret': 'dev-secret', 'persona': 'rich'}
    ).json()
    users_before = db.scalar(select(func.count()).select_from(User))

    second = client.post(
        '/dev/test_token', json={'secret': 'dev-secret', 'persona': 'rich'}
    ).json()
    users_after = db.scalar(select(func.count()).select_from(User))

    assert first['user_id'] == second['user_id']
    assert users_before == users_after


# --- Сид-логика get_or_create ---


def test_get_or_create_rich_builds_graph(db: Session):
    user = get_or_create_test_user(db, TestPersona.rich)

    assert user.is_test
    assert user.vk_id == '2000000001'
    assert user.vk_friends_data is not None and len(user.vk_friends_data) == 3
    # 2 друга в подписках, 1 остаётся в «возможных друзьях» (followed=False).
    assert len(user.follows) == 2
    # Один друг подписан на богатого юзера.
    assert len(user.followed_by) == 1
    # Свои желания (2 обычных + 4 WB во всех состояниях склада) + один
    # зарезервированный чужой.
    own = db.scalars(select(Wish).where(Wish.user_id == user.id)).all()
    assert len(own) == 6
    assert len(user.reserved_wishes) == 1
    store = {w.name: w for w in own if w.link}
    assert {
        (w.price_source, w.store_availability, w.price_is_minimum)
        for w in store.values()
    } == {
        (PriceSource.shop, PriceObservationStatus.ok, True),
        (PriceSource.shop, PriceObservationStatus.sold_out, False),
        (PriceSource.shop, PriceObservationStatus.gone, False),
        (PriceSource.manual, None, False),
    }
    # Распродано/исчез — последняя известная цена остаётся (0011); история —
    # по строке на сутки, без похода в магазин.
    assert store['Рюкзак городской'].price == 2999
    assert store['Термокружка'].price == 1490
    assert db.scalar(select(func.count()).select_from(WishPriceObservation)) == 6
    # Все сателлиты — тоже сид-юзеры.
    assert all(friend.is_test for friend in user.follows)


def test_get_or_create_rich_idempotent(db: Session):
    first = get_or_create_test_user(db, TestPersona.rich)
    users_after_first = db.scalar(select(func.count()).select_from(User))
    edges_after_first = db.scalar(
        select(func.count()).select_from(user_following_table)
    )

    wishes_after_first = db.scalar(select(func.count()).select_from(Wish))

    second = get_or_create_test_user(db, TestPersona.rich)

    assert first.id == second.id
    assert db.scalar(select(func.count()).select_from(User)) == users_after_first
    # WB-хотелки дописываются по ссылке — повтор их не дублирует.
    assert db.scalar(select(func.count()).select_from(Wish)) == wishes_after_first
    assert (
        db.scalar(select(func.count()).select_from(user_following_table))
        == edges_after_first
    )


def test_get_or_create_empty_is_bare(db: Session):
    user = get_or_create_test_user(db, TestPersona.empty)

    assert user.is_test
    assert user.vk_id is None
    assert user.vk_friends_data is None
    assert user.follows == []
    assert db.scalars(select(Wish).where(Wish.user_id == user.id)).all() == []


def test_get_or_create_empty_idempotent(db: Session):
    first = get_or_create_test_user(db, TestPersona.empty)
    second = get_or_create_test_user(db, TestPersona.empty)
    assert first.id == second.id


def test_build_test_token_format(db: Session, mocker):
    mocker.patch('app.config.settings.TEST_AUTH_SECRET', 'dev-secret')
    user = get_or_create_test_user(db, TestPersona.empty)
    assert build_test_token(user) == f'dev-secret:{user.id}'


def test_rich_store_observations_are_redated_on_each_call(db: Session):
    # e2e должен видеть «вчера/сегодня» независимо от дня: старый ряд сносится.
    user = get_or_create_test_user(db, TestPersona.rich)
    db.execute(
        update(WishPriceObservation).values(
            observed_date=WishPriceObservation.observed_date - timedelta(days=10)
        )
    )
    db.commit()

    get_or_create_test_user(db, TestPersona.rich)

    dates = set(db.scalars(select(WishPriceObservation.observed_date)))
    # Сид датирует наблюдения по UTC; локальное date.today() после полуночи
    # по местному времени уходит на день вперёд.
    today = utc_now().date()
    assert dates == {today, today - timedelta(days=1)}
    assert db.scalar(select(func.count()).select_from(WishPriceObservation)) == 6
    assert len([w for w in user.wishes if w.link]) == 4
