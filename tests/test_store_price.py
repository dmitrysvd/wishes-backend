"""Цена с магазина у хотелки (фича 0011): источник цены, свежий запрос к WB,
кнопка «актуальная с WB», превью с ценой, обход обновляет магазинные хотелки.

Магазин — реальный httpx-клиент на `httpx.MockTransport` через
`app.dependency_overrides[get_store_client]`: логика запроса и разбора ответа
прогоняется целиком, без моков внутри.
"""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import HttpUrl
from sqlalchemy import select

from app.constants import (
    PriceObservationStatus,
    PriceRefreshOutcome,
    PriceSource,
    Shop,
    StoreAvailability,
)
from app.cron_scripts.price_watch import crawl
from app.db import User, Wish, WishPriceObservation, WishPriceRefreshEvent
from app.dependencies import get_current_user, get_db, get_store_client
from app.helpers.price_watch import (
    ProductObservation,
    apply_observations,
    fetch_fresh_observation,
    observe_product,
    sync_wish_with_store,
)
from app.helpers.store_price import build_item_info
from app.main import app
from app.parsers import ParsedItemInfo
from app.utils import utc_now

FIXTURE = Path(__file__).parent / 'fixtures' / 'wb_cards_response.json'
# Карточка 100 из фикстуры: размеры 1001 (600 ₽), 1002 (550 ₽), 1003 распродан.
WB_SIZE_LINK = 'https://www.wildberries.ru/catalog/100/detail.aspx?size=1001'
WB_NO_SIZE_LINK = 'https://www.wildberries.ru/catalog/100/detail.aspx'
WB_SOLD_OUT_LINK = 'https://www.wildberries.ru/catalog/200/detail.aspx'
WB_GONE_LINK = 'https://www.wildberries.ru/catalog/300/detail.aspx'
OZON_LINK = 'https://www.ozon.ru/product/123456'
NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def store_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def fixture_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=FIXTURE.read_bytes())


def failing_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout('WB молчит')


@pytest.fixture
def user(db) -> User:
    user = User(
        display_name='Автор',
        email='author@test.ru',
        firebase_uid='author-uid',
        registered_at=utc_now(),
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def store(db, user):
    """Клиент API + переключатель ответа магазина (по умолчанию — фикстура WB)."""
    handlers = {'current': fixture_handler}

    def override_store_client():
        with store_client(lambda request: handlers['current'](request)) as client:
            yield client

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_store_client] = override_store_client
    yield handlers
    app.dependency_overrides = {}


@pytest.fixture
def api(store) -> TestClient:
    return TestClient(app)


def body(link: str | None, price: int | None, price_edited: bool | None = None) -> dict:
    data = {'name': 'Кроссовки', 'description': None, 'price': price, 'link': link}
    if price_edited is not None:
        data['price_edited'] = price_edited
    return data


def make_wish(db, user: User, **fields) -> Wish:
    wish = Wish(user_id=user.id, name='w', **fields)
    db.add(wish)
    db.commit()
    return wish


# --- чистая часть ----------------------------------------------------------


def test_observe_product_minimum_flag():
    from app.helpers.price_watch import WbCardResponseSchema

    response = WbCardResponseSchema.model_validate_json(FIXTURE.read_bytes())
    product = response.products[0]
    # Без размера у многоразмерного — минимум и «от».
    observation = observe_product(None, product)
    assert observation.status == PriceObservationStatus.ok
    assert observation.product_price == Decimal('550.00')
    assert observation.is_minimum is True
    # Конкретный размер — «от» нет.
    assert observe_product(1001, product).is_minimum is False


def test_fetch_fresh_observation_unsupported_link():
    assert fetch_fresh_observation(OZON_LINK, store_client(fixture_handler)) is None


def test_sync_wish_keeps_price_when_not_in_stock(db, user):
    wish = make_wish(db, user, link=WB_SIZE_LINK, price=Decimal('700'))
    wish.price_source = PriceSource.shop
    sync_wish_with_store(wish, ProductObservation(PriceObservationStatus.sold_out), NOW)
    assert wish.price == Decimal('700')
    assert wish.store_availability == PriceObservationStatus.sold_out
    assert wish.store_observation == {
        'availability': StoreAvailability.sold_out,
        'observed_at': NOW,
    }


def test_apply_observations_skips_manual(db, user):
    from app.helpers.price_watch import WatchTarget

    shop_wish = make_wish(db, user, link=WB_SIZE_LINK, price_source=PriceSource.shop)
    manual_wish = make_wish(db, user, link=WB_SIZE_LINK, price=Decimal('10'))
    observation = ProductObservation(PriceObservationStatus.sold_out)
    observed = [
        (WatchTarget(shop_wish.id, 100, 1001), observation),
        (WatchTarget(manual_wish.id, 100, 1001), observation),
    ]
    assert apply_observations(db, observed, NOW) == 1
    assert shop_wish.store_availability == PriceObservationStatus.sold_out
    assert manual_wish.store_availability is None
    assert manual_wish.store_observation is None


def test_crawl_updates_shop_wishes(db, user, mocker):
    mocker.patch('app.cron_scripts.price_watch.time.sleep')
    wish = make_wish(db, user, link=WB_NO_SIZE_LINK, price_source=PriceSource.shop)
    assert crawl(store_client(fixture_handler), NOW.date()) == 1
    db.refresh(wish)
    assert wish.price == Decimal('550')
    assert wish.price_is_minimum is True
    assert wish.store_availability == PriceObservationStatus.ok


# --- превью ------------------------------------------------------------------


def test_build_item_info_variants():
    parsed = ParsedItemInfo(
        title='t', description='', image_url=HttpUrl('https://img.test/1.jpg')
    )
    client = store_client(fixture_handler)
    ozon = build_item_info(parsed, OZON_LINK, client)
    assert (ozon.title, ozon.shop, ozon.price) == ('t', None, None)
    with_price = build_item_info(parsed, WB_NO_SIZE_LINK, client)
    assert (with_price.shop, with_price.price, with_price.price_is_minimum) == (
        Shop.wildberries,
        550,
        True,
    )
    sold_out = build_item_info(parsed, WB_SOLD_OUT_LINK, client)
    assert (sold_out.shop, sold_out.price) == (Shop.wildberries, None)
    failed = build_item_info(parsed, WB_SIZE_LINK, store_client(failing_handler))
    assert (failed.shop, failed.price) == (Shop.wildberries, None)


# --- POST /wishes --------------------------------------------------------------


def test_add_wish_wb_link_takes_store_price(api, db):
    response = api.post('/wishes', json=body(WB_SIZE_LINK, 1, price_edited=False))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['price'] == 600
    assert data['price_source'] == 'shop'
    assert data['shop'] == 'wildberries'
    assert data['price_is_minimum'] is False
    assert data['store_observation']['availability'] == 'in_stock'
    # История наблюдений пополнена свежим запросом.
    assert db.scalar(select(WishPriceObservation.status)) == PriceObservationStatus.ok


def test_add_wish_wb_link_manual_wins(api):
    data = api.post('/wishes', json=body(WB_SIZE_LINK, 4500, price_edited=True)).json()
    assert (data['price'], data['price_source']) == (4500, 'manual')
    assert data['shop'] == 'wildberries'
    assert data['store_observation'] is None


def test_add_wish_wb_store_down_uses_preview_fallback(api, store):
    store['current'] = failing_handler
    data = api.post('/wishes', json=body(WB_SIZE_LINK, 4990)).json()
    assert (data['price'], data['price_source']) == (4990, 'shop')
    assert data['store_observation'] is None
    assert data['price_is_minimum'] is False


def test_add_wish_wb_sold_out_no_price(api):
    data = api.post('/wishes', json=body(WB_SOLD_OUT_LINK, 999)).json()
    assert data['price'] is None
    assert data['store_observation']['availability'] == 'sold_out'


def test_add_wish_unsupported_link_is_manual(api):
    data = api.post('/wishes', json=body(OZON_LINK, 3500)).json()
    assert (data['price'], data['price_source'], data['shop']) == (3500, 'manual', None)


# --- PUT /wishes/{id} ----------------------------------------------------------


@pytest.fixture
def shop_wish(db, user) -> Wish:
    return make_wish(
        db,
        user,
        link=WB_SIZE_LINK,
        price=Decimal('600'),
        price_source=PriceSource.shop,
        store_availability=PriceObservationStatus.ok,
        store_observed_at=NOW,
    )


def test_update_echo_keeps_store_price(api, shop_wish):
    # Фронт переслал устаревшую цену — это не правка.
    data = api.put(f'/wishes/{shop_wish.id}', json=body(WB_SIZE_LINK, 1)).json()
    assert (data['price'], data['price_source']) == (600, 'shop')
    assert data['store_observation']['observed_at'].startswith('2026-09-12T12:00')


def test_update_price_edited_makes_manual(api, shop_wish):
    data = api.put(
        f'/wishes/{shop_wish.id}', json=body(WB_SIZE_LINK, 600, price_edited=True)
    ).json()
    assert (data['price'], data['price_source']) == (600, 'manual')
    assert data['store_observation'] is None
    erased = api.put(
        f'/wishes/{shop_wish.id}', json=body(WB_SIZE_LINK, None, price_edited=True)
    ).json()
    assert erased['price'] is None


def test_update_link_change_to_wb_refetches(api, shop_wish):
    data = api.put(f'/wishes/{shop_wish.id}', json=body(WB_NO_SIZE_LINK, 600)).json()
    assert (data['price'], data['price_is_minimum']) == (550, True)


def test_update_link_change_to_wb_store_down_gives_null(api, shop_wish, store):
    store['current'] = failing_handler
    data = api.put(f'/wishes/{shop_wish.id}', json=body(WB_NO_SIZE_LINK, 600)).json()
    assert (data['price'], data['price_source'], data['store_observation']) == (
        None,
        'shop',
        None,
    )


def test_update_link_change_to_wb_with_manual_price(api, shop_wish):
    data = api.put(
        f'/wishes/{shop_wish.id}', json=body(WB_NO_SIZE_LINK, 777, price_edited=True)
    ).json()
    assert (data['price'], data['price_source'], data['store_observation']) == (
        777,
        'manual',
        None,
    )


def test_update_link_change_to_unsupported_keeps_price(api, shop_wish):
    shop_wish.price_is_minimum = True
    data = api.put(f'/wishes/{shop_wish.id}', json=body(OZON_LINK, 1)).json()
    assert (data['price'], data['price_source'], data['shop']) == (600, 'manual', None)
    assert data['price_is_minimum'] is False
    removed = api.put(f'/wishes/{shop_wish.id}', json=body(None, 1)).json()
    assert (removed['price'], removed['link']) == (600, None)


def test_update_manual_wish_applies_body_price(api, db, user):
    wish = make_wish(db, user, link=OZON_LINK, price=Decimal('100'))
    data = api.put(f'/wishes/{wish.id}', json=body(OZON_LINK, 250)).json()
    assert (data['price'], data['price_source']) == (250, 'manual')


# --- POST /wishes/{id}/refresh_store_price --------------------------------------


def refresh_outcomes(db) -> list[PriceRefreshOutcome]:
    return list(db.scalars(select(WishPriceRefreshEvent.outcome)).all())


def test_refresh_returns_manual_wish_to_store(api, db, user):
    wish = make_wish(db, user, link=WB_SIZE_LINK, price=Decimal('4500'))
    response = api.post(f'/wishes/{wish.id}/refresh_store_price')
    assert response.status_code == 200, response.text
    data = response.json()
    assert (data['price'], data['price_source']) == (600, 'shop')
    assert data['store_observation']['availability'] == 'in_stock'
    assert refresh_outcomes(db) == [PriceRefreshOutcome.ok]
    # Повтор при магазинном источнике безвреден.
    assert api.post(f'/wishes/{wish.id}/refresh_store_price').status_code == 200


def test_refresh_sold_out_keeps_manual_number(api, db, user):
    wish = make_wish(db, user, link=WB_SOLD_OUT_LINK, price=Decimal('4500'))
    data = api.post(f'/wishes/{wish.id}/refresh_store_price').json()
    assert (data['price'], data['price_source']) == (4500, 'shop')
    assert data['store_observation']['availability'] == 'sold_out'


def test_refresh_unsupported_link_409(api, db, user):
    wish = make_wish(db, user, link=OZON_LINK)
    response = api.post(f'/wishes/{wish.id}/refresh_store_price')
    assert response.status_code == 409
    assert refresh_outcomes(db) == [PriceRefreshOutcome.unsupported]


def test_refresh_store_down_502_changes_nothing(api, db, user, store):
    store['current'] = failing_handler
    wish = make_wish(db, user, link=WB_SIZE_LINK, price=Decimal('4500'))
    response = api.post(f'/wishes/{wish.id}/refresh_store_price')
    assert response.status_code == 502
    db.refresh(wish)
    assert (wish.price, wish.price_source) == (Decimal('4500'), PriceSource.manual)
    assert refresh_outcomes(db) == [PriceRefreshOutcome.failed]


# --- превью через API --------------------------------------------------------------


def test_item_info_from_page_adds_price(api, mocker):
    mocker.patch(
        'app.routers.users.try_parse_item_by_link',
        return_value=ParsedItemInfo(
            title='Кроссовки',
            description='',
            image_url=HttpUrl('https://img.test/1.jpg'),
        ),
    )
    data = api.post('/item_info_from_page', json={'link': WB_NO_SIZE_LINK}).json()
    assert (data['shop'], data['price'], data['price_is_minimum']) == (
        'wildberries',
        550,
        True,
    )


# --- публичный вишлист ------------------------------------------------------------


def test_public_wishlist_has_price_is_minimum(api, shop_wish, user):
    shop_wish.price_is_minimum = True
    data = api.get(f'/public/users/{user.id}/wishlist').json()
    assert data['wishes'][0]['price_is_minimum'] is True
