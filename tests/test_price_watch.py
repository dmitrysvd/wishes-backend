import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.constants import PriceObservationStatus, Shop
from app.cron_scripts.price_watch import crawl, main
from app.db import User, Wish, WishPriceObservation
from app.helpers.price_watch import (
    WatchTarget,
    WbCardResponseSchema,
    batched,
    build_observations,
    fetch_wb_cards,
    save_observations,
    select_watch_targets,
)
from app.parsers import parse_wildberries_link
from app.utils import utc_now

FIXTURE = Path(__file__).parent / 'fixtures' / 'wb_cards_response.json'
TODAY = date(2026, 9, 10)


@pytest.fixture
def wb_response() -> WbCardResponseSchema:
    return WbCardResponseSchema.model_validate(json.loads(FIXTURE.read_text()))


def wb_client(handler) -> httpx.Client:
    # Реальный httpx-клиент на MockTransport: логика запроса без моков.
    return httpx.Client(transport=httpx.MockTransport(handler))


def fixture_client() -> httpx.Client:
    return wb_client(lambda request: httpx.Response(200, content=FIXTURE.read_bytes()))


@pytest.fixture
def user(db) -> User:
    user = User(
        firebase_uid=f'uid-{uuid4()}',
        display_name='Тест',
        email=f'{uuid4()}@t.ru',
        registered_at=utc_now(),
    )
    db.add(user)
    db.commit()
    return user


def make_wish(db, user: User, link: str | None, is_archived: bool = False) -> Wish:
    wish = Wish(user_id=user.id, name='w', link=link, is_archived=is_archived)
    db.add(wish)
    db.commit()
    return wish


@pytest.mark.parametrize(
    'link, expected',
    [
        ('https://www.wildberries.ru/catalog/100/detail.aspx?size=1001', (100, 1001)),
        ('https://wildberries.ru/catalog/100/detail.aspx', (100, None)),
        ('https://www.wildberries.ru/catalog/100/detail.aspx?a=1&size=7', (100, 7)),
        ('https://www.wildberries.ru/brands/x', None),
        ('https://www.ozon.ru/product/100', None),
    ],
)
def test_parse_wildberries_link(link, expected):
    assert parse_wildberries_link(link) == expected


def test_select_watch_targets(db, user):
    wb = make_wish(
        db, user, 'https://www.wildberries.ru/catalog/100/detail.aspx?size=1001'
    )
    make_wish(
        db, user, 'https://www.wildberries.ru/catalog/300/detail.aspx', is_archived=True
    )
    make_wish(db, user, 'https://www.ozon.ru/product/1')
    make_wish(db, user, None)
    assert select_watch_targets(db) == [WatchTarget(wb.id, 100, 1001)]


def test_batched():
    targets = [WatchTarget(uuid4(), i, None) for i in range(5)]
    assert [len(b) for b in batched(targets, 2)] == [2, 2, 1]
    assert list(batched([], 2)) == []


def test_fetch_wb_cards_sends_skus_and_parses(wb_response):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen['nm'] = request.url.params['nm']
        return httpx.Response(200, content=FIXTURE.read_bytes())

    assert fetch_wb_cards([100, 200], wb_client(handler)) == wb_response
    assert seen['nm'] == '100;200'


def test_fetch_wb_cards_http_error():
    with pytest.raises(httpx.HTTPStatusError):
        fetch_wb_cards([1], wb_client(lambda r: httpx.Response(500)))


def test_fetch_wb_cards_bad_format():
    with pytest.raises(ValueError):
        fetch_wb_cards([1], wb_client(lambda r: httpx.Response(200, json={'x': 1})))


def test_build_observations_states(wb_response):
    batch = [
        WatchTarget(uuid4(), 100, 1001),  # размер из ссылки, в наличии
        WatchTarget(uuid4(), 100, 1003),  # размер из ссылки, распродан
        WatchTarget(uuid4(), 100, 1999),  # размера в карточке больше нет
        WatchTarget(uuid4(), 100, None),  # без размера → min среди в наличии
        WatchTarget(uuid4(), 200, None),  # все размеры распроданы
        WatchTarget(uuid4(), 300, None),  # артикула нет в ответе
    ]
    rows = build_observations(batch, wb_response, TODAY)
    by_status = [(r['status'], r['basic_price'], r['product_price']) for r in rows]
    assert by_status == [
        (PriceObservationStatus.ok, Decimal('1508.00'), Decimal('600.00')),
        (PriceObservationStatus.sold_out, None, None),
        (PriceObservationStatus.gone, None, None),
        (PriceObservationStatus.ok, Decimal('1508.00'), Decimal('550.00')),
        (PriceObservationStatus.sold_out, None, None),
        (PriceObservationStatus.gone, None, None),
    ]
    assert rows[0]['wish_id'] == batch[0].wish_id
    assert rows[0]['shop'] is Shop.wildberries
    assert rows[0]['sku'] == 100
    assert rows[0]['size_option_id'] == 1001
    assert rows[0]['observed_date'] == TODAY


def test_save_observations_idempotent(db, user, wb_response):
    wish = make_wish(
        db, user, 'https://www.wildberries.ru/catalog/100/detail.aspx?size=1001'
    )
    batch = [WatchTarget(wish.id, 100, 1001)]
    rows = build_observations(batch, wb_response, TODAY)
    assert save_observations(db, rows) == 1
    # Повтор за те же сутки: первое наблюдение — истина, вторая строка не плодится.
    rows[0]['product_price'] = Decimal('1.00')
    assert save_observations(db, rows) == 0
    saved = db.scalars(select(WishPriceObservation)).all()
    assert len(saved) == 1
    assert saved[0].product_price == Decimal('600.00')
    assert save_observations(db, []) == 0


def test_crawl_saves_and_skips_failed_batch(db, user, mocker):
    wishes = [
        make_wish(
            db, user, 'https://www.wildberries.ru/catalog/100/detail.aspx?size=1001'
        ),
        make_wish(db, user, 'https://www.wildberries.ru/catalog/200/detail.aspx'),
        make_wish(db, user, 'https://www.wildberries.ru/catalog/300/detail.aspx'),
    ]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params['nm'])
        # Второй батч падает — обход должен продолжиться и записать остальные.
        if len(calls) == 2:
            return httpx.Response(503)
        return httpx.Response(200, content=FIXTURE.read_bytes())

    sleep = mocker.patch('app.cron_scripts.price_watch.time.sleep')
    saved = crawl(wb_client(handler), TODAY, batch_size=1, pause_seconds=0.5)

    assert calls == ['100', '200', '300']
    assert saved == 2
    # Пауза — между батчами, не перед первым.
    assert sleep.call_count == 2
    rows = {r.wish_id: r.status for r in db.scalars(select(WishPriceObservation)).all()}
    assert rows == {
        wishes[0].id: PriceObservationStatus.ok,
        wishes[2].id: PriceObservationStatus.gone,
    }


def test_crawl_all_failed_logs_error(db, user, mocker):
    make_wish(db, user, 'https://www.wildberries.ru/catalog/100/detail.aspx')
    logger = mocker.patch('app.cron_scripts.price_watch.logger')
    assert crawl(wb_client(lambda r: httpx.Response(500)), TODAY) == 0
    logger.error.assert_called_once()


def test_crawl_nothing_to_watch(db):
    assert crawl(fixture_client(), TODAY) == 0


def test_main_runs_crawl(mocker):
    crawl_mock = mocker.patch('app.cron_scripts.price_watch.crawl', return_value=0)
    main()
    crawl_mock.assert_called_once()
    assert isinstance(crawl_mock.call_args.args[0], httpx.Client)


def test_script_main_execution(mocker):
    import os
    import runpy

    from app.cron_scripts import price_watch

    # runpy импортирует модуль заново как __main__, поэтому глушим саму рабочую
    # функцию в helpers — свежий импорт внутри скрипта подхватит подмену и обход
    # не пойдёт ни в сеть, ни в БД.
    mocker.patch('app.helpers.price_watch.select_watch_targets', return_value=[])
    runpy.run_path(os.path.abspath(price_watch.__file__), run_name='__main__')
