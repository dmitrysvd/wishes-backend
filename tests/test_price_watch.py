import json
import random
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.constants import PriceObservationStatus, Shop
from app.cron_scripts.price_watch import crawl_tick, main, report_coverage
from app.db import User, Wish, WishPriceObservation
from app.helpers.price_watch import (
    WatchTarget,
    WbCardResponseSchema,
    build_observations,
    fetch_wb_cards,
    save_observations,
    select_pending_targets,
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


def test_select_pending_targets_skips_observed_today(db, user, wb_response):
    seen = make_wish(db, user, 'https://www.wildberries.ru/catalog/100/detail.aspx')
    fresh = make_wish(db, user, 'https://www.wildberries.ru/catalog/200/detail.aspx')
    save_observations(
        db, build_observations([WatchTarget(seen.id, 100, None)], wb_response, TODAY)
    )
    assert [t.wish_id for t in select_pending_targets(db, TODAY)] == [fresh.id]
    # Вчерашнее наблюдение сегодняшний обход не отменяет.
    tomorrow = date(2026, 9, 11)
    assert {t.wish_id for t in select_pending_targets(db, tomorrow)} == {
        seen.id,
        fresh.id,
    }


def test_fetch_wb_cards_sends_skus_and_parses(wb_response):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen['nm'] = request.url.params['nm']
        seen['origin'] = request.headers.get('origin')
        return httpx.Response(200, content=FIXTURE.read_bytes())

    assert fetch_wb_cards([100, 200], wb_client(handler)) == wb_response
    assert seen['nm'] == '100;200'
    # Запрос выглядит как от витрины WB, а не голый API-клиент.
    assert seen['origin'] == 'https://www.wildberries.ru'


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


def wb_links(db, user, *skus: int) -> list[Wish]:
    return [
        make_wish(db, user, f'https://www.wildberries.ru/catalog/{sku}/detail.aspx')
        for sku in skus
    ]


def test_crawl_tick_takes_one_batch_until_all_observed(db, user):
    wishes = wb_links(db, user, 100, 200, 300)
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.params['nm'].split(';'))
        return httpx.Response(200, content=FIXTURE.read_bytes())

    client, rng = wb_client(handler), random.Random(0)
    assert crawl_tick(client, TODAY, batch_size=2, rng=rng) == 2
    assert crawl_tick(client, TODAY, batch_size=2, rng=rng) == 1
    # Всё наблюдено — тик не ходит в сеть.
    assert crawl_tick(client, TODAY, batch_size=2, rng=rng) == 0
    # Один тик — один запрос; каждый артикул запрошен ровно раз.
    assert [len(nm) for nm in requests] == [2, 1]
    assert sorted(sum(requests, [])) == ['100', '200', '300']
    rows = {r.wish_id for r in db.scalars(select(WishPriceObservation)).all()}
    assert rows == {w.id for w in wishes}


def test_crawl_tick_failed_batch_is_retried_next_tick(db, user, mocker):
    (wish,) = wb_links(db, user, 100)
    logger = mocker.patch('app.cron_scripts.price_watch.logger')
    blocked = wb_client(
        lambda r: httpx.Response(
            403,
            headers={'status-no-id': 'PG-42-XC', 'x-pow': 'status=invalid'},
            content=b'<html>403 Forbidden</html>',
        )
    )
    assert crawl_tick(blocked, TODAY) == 0
    # В логе — метки антибота, по которым видно, кто режет.
    message = logger.warning.call_args.args[0]
    assert 'HTTP 403' in message
    assert 'PG-42-XC' in message
    assert 'status=invalid' in message
    assert '403 Forbidden' in message
    assert select_pending_targets(db, TODAY) == [WatchTarget(wish.id, 100, None)]
    assert crawl_tick(fixture_client(), TODAY) == 1
    assert select_pending_targets(db, TODAY) == []


def test_crawl_tick_bad_format_logged_as_is(db, user, mocker):
    wb_links(db, user, 100)
    logger = mocker.patch('app.cron_scripts.price_watch.logger')
    client = wb_client(lambda r: httpx.Response(200, json={'x': 1}))
    assert crawl_tick(client, TODAY) == 0
    assert 'WbCardResponseSchema' in logger.warning.call_args.args[0]


def test_crawl_tick_nothing_to_watch(db):
    assert crawl_tick(fixture_client(), TODAY) == 0


def test_report_coverage(db, user, mocker, wb_response):
    logger = mocker.patch('app.cron_scripts.price_watch.logger')
    assert report_coverage(TODAY) == (0, 0)
    logger.error.assert_not_called()

    seen, _ = wb_links(db, user, 100, 200)
    assert report_coverage(TODAY) == (0, 2)
    logger.error.assert_called_once()

    save_observations(
        db, build_observations([WatchTarget(seen.id, 100, None)], wb_response, TODAY)
    )
    assert report_coverage(TODAY) == (1, 2)
    logger.error.assert_called_once()


def test_main_runs_crawl_tick(mocker):
    tick = mocker.patch('app.cron_scripts.price_watch.crawl_tick', return_value=0)
    main()
    tick.assert_called_once()
    assert isinstance(tick.call_args.args[0], httpx.Client)


def test_script_main_execution(mocker):
    import os
    import runpy

    from app.cron_scripts import price_watch

    # runpy импортирует модуль заново как __main__, поэтому глушим саму рабочую
    # функцию в helpers — свежий импорт внутри скрипта подхватит подмену и обход
    # не пойдёт ни в сеть, ни в БД.
    mocker.patch('app.helpers.price_watch.select_pending_targets', return_value=[])
    runpy.run_path(os.path.abspath(price_watch.__file__), run_name='__main__')


def test_save_observations_ids_beyond_int32(db, user, wb_response):
    # WB-счётчики уже перевалили за int32: optionId 2 220 626 238 на проде валил
    # вставку с NumericValueOutOfRange. Колонки — BIGINT.
    wish = make_wish(
        db, user, 'https://www.wildberries.ru/catalog/100/detail.aspx?size=1001'
    )
    rows = build_observations([WatchTarget(wish.id, 100, 1999)], wb_response, TODAY)
    rows[0]['sku'] = 3_000_000_000
    rows[0]['size_option_id'] = 2_220_626_238
    assert save_observations(db, rows) == 1
    saved = db.scalars(select(WishPriceObservation)).one()
    assert (saved.sku, saved.size_option_id) == (3_000_000_000, 2_220_626_238)
