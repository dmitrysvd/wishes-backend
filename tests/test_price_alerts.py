"""Пуши по складу (фича 0013): триггеры, база «видел», дайджест, один в сутки,
сброс базы только при принятом FCM, открытие по пушу."""

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import (
    NotificationGroup,
    PriceAlertTrigger,
    PriceObservationStatus,
    PriceSource,
    Shop,
)
from app.db import (
    PushReason,
    PushSendingLog,
    User,
    UserActivityDay,
    Wish,
    WishPriceObservation,
)
from app.main import app, get_current_user, get_db
from app.notification_settings import set_group_enabled
from app.price_alerts import (
    build_message,
    detect_alert,
    rubles,
    seen_price,
    send_price_alerts,
)
from app.utils import utc_now

WB = 'https://www.wildberries.ru/catalog/{sku}/detail.aspx'
TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)


@pytest.fixture(autouse=True)
def price_alerts_on(monkeypatch):
    """Пуши по складу выключены продуктом до выкладки Android с 0011; тесты
    логики включают флаг явно — это конфигурация системы, не мок."""
    monkeypatch.setattr('app.price_alerts.PRICE_ALERT_ENABLED', True)


@pytest.fixture
def user(db: Session) -> User:
    user = User(
        display_name='Автор',
        firebase_uid='author-uid',
        firebase_push_token='token-author',
        registered_at=utc_now(),
    )
    db.add(user)
    db.commit()
    return user


def make_wish(db: Session, user: User, sku: int = 1, **fields) -> Wish:
    fields.setdefault('link', WB.format(sku=sku))
    fields.setdefault('price_source', PriceSource.shop)
    wish = Wish(user_id=user.id, name=f'Вещь {sku}', **fields)
    db.add(wish)
    db.commit()
    return wish


def observe(
    db: Session,
    wish: Wish,
    day: date,
    price: int | None,
    status: PriceObservationStatus = PriceObservationStatus.ok,
) -> None:
    db.add(
        WishPriceObservation(
            wish_id=wish.id,
            observed_date=day,
            shop=Shop.wildberries,
            sku=1,
            status=status,
            product_price=Decimal(price) if price is not None else None,
            basic_price=Decimal(price) if price is not None else None,
        )
    )
    if status == PriceObservationStatus.ok and price is not None:
        wish.price = Decimal(price)
    wish.store_availability = status
    db.commit()


def active(db: Session, user: User, day: date) -> None:
    now = utc_now()
    db.add(
        UserActivityDay(
            user_id=user.id, activity_date=day, first_seen_at=now, last_seen_at=now
        )
    )
    db.commit()


def logs(db: Session) -> list[PushSendingLog]:
    return list(
        db.scalars(
            select(PushSendingLog).where(
                PushSendingLog.reason == PushReason.PRICE_ALERT
            )
        )
    )


# --- База «видел» --------------------------------------------------------------


def test_seen_price_prefers_activity_day_observation(db, user):
    wish = make_wish(db, user)
    observe(db, wish, TODAY - timedelta(days=3), 3000)
    observe(db, wish, YESTERDAY, 2900)
    observe(db, wish, TODAY, 2500)
    # Активен был вчера — видел вчерашнюю цену, не сегодняшнюю и не позавчерашнюю.
    assert seen_price(db, wish, YESTERDAY) == 2900


def test_seen_price_takes_newer_of_base_and_activity(db, user):
    wish = make_wish(db, user)
    observe(db, wish, TODAY - timedelta(days=3), 3000)
    observe(db, wish, TODAY, 2500)
    # Пуш/кнопка были позже последней активности → база из них.
    wish.alert_base_price = Decimal(2800)
    wish.alert_base_at = utc_now()
    db.commit()
    assert seen_price(db, wish, TODAY - timedelta(days=3)) == 2800
    # …а если активность позже базы — побеждает наблюдение на день активности.
    wish.alert_base_at = utc_now() - timedelta(days=5)
    db.commit()
    assert seen_price(db, wish, TODAY - timedelta(days=3)) == 3000


def test_seen_price_falls_back_to_first_observation(db, user):
    wish = make_wish(db, user)
    observe(db, wish, YESTERDAY, 3000)
    observe(db, wish, TODAY, 2500)
    # Никогда не был активен и базы нет — цена, с которой хотелка началась.
    assert seen_price(db, wish, None) == 3000


def test_seen_price_skips_observation_without_price(db, user):
    wish = make_wish(db, user)
    observe(db, wish, TODAY - timedelta(days=3), None, PriceObservationStatus.gone)
    observe(db, wish, TODAY - timedelta(days=2), 3000)
    observe(db, wish, YESTERDAY, None, PriceObservationStatus.sold_out)
    # В день активности товар был распродан — базой быть не может → первое
    # наблюдение С ценой (не самое первое, оно без цены).
    assert seen_price(db, wish, YESTERDAY) == 3000


def test_things_declension():
    from app.price_alerts import _things

    assert [_things(n) for n in (1, 2, 5, 11, 21, 22, 25)] == [
        '1 вещь',
        '2 вещи',
        '5 вещей',
        '11 вещей',
        '21 вещь',
        '22 вещи',
        '25 вещей',
    ]


# --- Триггеры ------------------------------------------------------------------


def test_price_drop_at_threshold_triggers(db, user):
    wish = make_wish(db, user)
    observe(db, wish, YESTERDAY, 3000)
    observe(db, wish, TODAY, 2700)
    alert = detect_alert(db, wish, TODAY, YESTERDAY)
    assert alert is not None
    assert (alert.trigger, alert.price, alert.was_price) == (
        PriceAlertTrigger.price,
        2700,
        3000,
    )


def test_small_drop_and_rise_are_silent(db, user):
    wish = make_wish(db, user)
    observe(db, wish, YESTERDAY, 3000)
    observe(db, wish, TODAY, 2701)
    assert detect_alert(db, wish, TODAY, YESTERDAY) is None
    other = make_wish(db, user, sku=2)
    observe(db, other, YESTERDAY, 3000)
    observe(db, other, TODAY, 3500)
    assert detect_alert(db, other, TODAY, YESTERDAY) is None


def test_slow_drift_below_threshold_from_seen_price_triggers(db, user):
    # Три дня по −4%: от вчерашнего не событие, от того, что ВИДЕЛ, — событие.
    wish = make_wish(db, user)
    observe(db, wish, TODAY - timedelta(days=3), 3000)
    observe(db, wish, TODAY - timedelta(days=2), 2880)
    observe(db, wish, YESTERDAY, 2765)
    observe(db, wish, TODAY, 2654)
    assert detect_alert(db, wish, TODAY, TODAY - timedelta(days=3)) is not None
    assert detect_alert(db, wish, TODAY, YESTERDAY) is None


@pytest.fixture
def availability_on(monkeypatch):
    """Триггер «снова в наличии» выключен продуктом; тесты его логики
    включают флаг явно — это конфигурация системы, не мок."""
    monkeypatch.setattr('app.price_alerts.PRICE_ALERT_AVAILABILITY_ENABLED', True)


def test_back_in_stock_disabled_by_default(db, user):
    wish = make_wish(db, user)
    observe(db, wish, TODAY - timedelta(days=2), 3000)
    observe(db, wish, YESTERDAY, None, PriceObservationStatus.sold_out)
    observe(db, wish, TODAY, 2000)
    # Возврат в наличие — молчим (и «подешевело» от цены до распродажи не считаем).
    assert detect_alert(db, wish, TODAY, TODAY - timedelta(days=2)) is None


def test_back_in_stock_triggers_and_wins_over_price(db, user, availability_on):
    wish = make_wish(db, user)
    observe(db, wish, TODAY - timedelta(days=2), 3000)
    observe(db, wish, YESTERDAY, None, PriceObservationStatus.sold_out)
    observe(db, wish, TODAY, 2000)
    alert = detect_alert(db, wish, TODAY, TODAY - timedelta(days=2))
    assert alert is not None
    assert alert.trigger == PriceAlertTrigger.availability
    assert alert.was_price is None


def test_no_observation_today_means_no_alert(db, user):
    # Обход упал — задним числом не догоняем.
    wish = make_wish(db, user)
    observe(db, wish, TODAY - timedelta(days=2), 3000)
    observe(db, wish, YESTERDAY, 2000)
    assert detect_alert(db, wish, TODAY, None) is None


# --- Тексты ----------------------------------------------------------------------


def test_short_name_truncates_with_ellipsis():
    from app.price_alerts import short_name

    assert short_name('Кружка для чая') == 'Кружка для чая'
    long = 'Ободок для макияжа и умывания с нарукавниками махровый'
    assert short_name(long) == 'Ободок для макияжа и умывания с нарукав…'
    assert len(short_name(long)) == 40


def test_rubles_format():
    assert rubles(Decimal('2700')) == '2 700 ₽'
    assert rubles(Decimal('999.99')) == '999 ₽'
    assert rubles(Decimal('1234567')) == '1 234 567 ₽'


def test_message_texts(db, user, availability_on):
    a = make_wish(db, user, sku=1)
    a.name = 'Кроссовки для бега'
    b = make_wish(db, user, sku=2)
    observe(db, a, YESTERDAY, 3000)
    observe(db, a, TODAY, 2700)
    observe(db, b, YESTERDAY, None, PriceObservationStatus.gone)
    observe(db, b, TODAY, 1490)
    price_alert = detect_alert(db, a, TODAY, YESTERDAY)
    stock_alert = detect_alert(db, b, TODAY, YESTERDAY)
    assert price_alert and stock_alert

    title, body, link, trigger = build_message(user, [price_alert])
    assert (title, body) == (
        '„Кроссовки для бега“ подешевела',
        '2 700 ₽ вместо 3 000 ₽',
    )
    assert link.endswith(f'/wish?wishId={a.id}')
    assert trigger == PriceAlertTrigger.price

    title, body, link, trigger = build_message(user, [stock_alert])
    assert (title, body) == ('„Вещь 2“ снова в наличии', '1 490 ₽ на WB')
    assert trigger == PriceAlertTrigger.availability

    title, body, link, trigger = build_message(user, [price_alert, stock_alert])
    assert title == '2 вещи из списка подешевели или вернулись в наличие'
    assert body == '„Кроссовки для бега“: 2 700 ₽ вместо 3 000 ₽'
    assert f'userId={user.id}' in link
    assert trigger == PriceAlertTrigger.mixed
    # Пять одинаковых — «вещей», тип не mixed.
    title, _, _, trigger = build_message(user, [price_alert] * 5)
    assert title.startswith('5 вещей')
    assert trigger == PriceAlertTrigger.price


# --- Крон: отправка, дедуп, база --------------------------------------------------


def test_send_price_alerts_sends_digest_and_moves_base(db, user, fcm):
    wish = make_wish(db, user)
    observe(db, wish, YESTERDAY, 3000)
    observe(db, wish, TODAY, 2700)

    assert send_price_alerts(TODAY) == 1

    (message,) = fcm.messages
    assert message.token == 'token-author'
    data = message.data
    assert (data['type'], data['trigger']) == ('price_alert', 'price')
    assert data['title'] == '„Вещь 1“ подешевела'
    assert data['link'].endswith(f'/wish?wishId={wish.id}')
    (log,) = logs(db)
    assert str(log.id) == data['delivery_id']
    assert log.trigger == PriceAlertTrigger.price
    assert log.opened_at is None
    # FCM принял → база = цена из пуша.
    db.refresh(wish)
    assert wish.alert_base_price == 2700

    # Тот же день (перезапуск обхода) — второго пуша нет.
    assert send_price_alerts(TODAY) == 0
    assert len(fcm.messages) == 1


def test_master_switch_off_does_nothing(db, user, fcm, monkeypatch):
    # Выключено целиком: не считает, не шлёт, не пишет лог, базу не двигает.
    monkeypatch.setattr('app.price_alerts.PRICE_ALERT_ENABLED', False)
    wish = make_wish(db, user)
    observe(db, wish, YESTERDAY, 3000)
    observe(db, wish, TODAY, 2700)

    assert send_price_alerts(TODAY) == 0

    assert fcm.calls == [] and logs(db) == []
    db.refresh(wish)
    assert wish.alert_base_price is None


def test_repeat_push_only_after_another_threshold_drop(db, user, fcm):
    wish = make_wish(db, user)
    observe(db, wish, YESTERDAY, 3000)
    observe(db, wish, TODAY, 2700)
    send_price_alerts(TODAY)
    fcm.clear()

    tomorrow = TODAY + timedelta(days=1)
    observe(db, wish, tomorrow, 2500)  # −7% от 2700 — молчим
    assert send_price_alerts(tomorrow) == 0
    day_after = tomorrow + timedelta(days=1)
    observe(db, wish, day_after, 2400)  # −11% от 2700 — событие
    assert send_price_alerts(day_after) == 1


def test_base_not_moved_when_fcm_rejects(db, user, fcm, mocker):
    wish = make_wish(db, user)
    observe(db, wish, YESTERDAY, 3000)
    observe(db, wish, TODAY, 2700)

    def rejecting(messages, dry_run=False):
        responses = [
            SimpleNamespace(success=False, exception=RuntimeError('quota'))
            for _ in messages
        ]
        return SimpleNamespace(
            responses=responses, success_count=0, failure_count=len(responses)
        )

    mocker.patch('app.firebase.messaging.send_each', rejecting)
    send_price_alerts(TODAY)
    db.refresh(wish)
    assert wish.alert_base_price is None
    # Лог отправки при этом есть: дедуп «один в сутки» держится на нём.
    assert len(logs(db)) == 1


def test_disabled_group_sends_nothing_and_keeps_base(db, user, fcm):
    wish = make_wish(db, user)
    observe(db, wish, YESTERDAY, 3000)
    observe(db, wish, TODAY, 2700)
    set_group_enabled(db, user, NotificationGroup.prices, False)

    assert send_price_alerts(TODAY) == 0
    assert fcm.messages == []
    assert logs(db) == []
    db.refresh(wish)
    assert wish.alert_base_price is None


def test_manual_and_archived_wishes_are_ignored(db, user, fcm):
    manual = make_wish(db, user, sku=1, price_source=PriceSource.manual)
    archived = make_wish(db, user, sku=2, is_archived=True)
    for wish in (manual, archived):
        observe(db, wish, YESTERDAY, 3000)
        observe(db, wish, TODAY, 2000)
    assert send_price_alerts(TODAY) == 0


def test_user_without_activity_compares_with_start_price(db, user, fcm):
    wish = make_wish(db, user)
    observe(db, wish, TODAY - timedelta(days=2), 3000)
    observe(db, wish, YESTERDAY, 2950)
    observe(db, wish, TODAY, 2650)
    assert send_price_alerts(TODAY) == 1


def test_activity_today_means_already_seen(db, user, fcm):
    wish = make_wish(db, user)
    observe(db, wish, YESTERDAY, 3000)
    observe(db, wish, TODAY, 2700)
    active(db, user, TODAY)
    # Открывал приложение сегодня — сегодняшнюю цену уже видел.
    assert send_price_alerts(TODAY) == 0


# --- Включение группы сбрасывает базу ------------------------------------------------


@pytest.fixture
def client(db: Session, user: User) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    yield TestClient(app)
    app.dependency_overrides = {}


def test_enabling_prices_group_resets_base_to_card_price(client, db, user):
    wish = make_wish(db, user)
    observe(db, wish, YESTERDAY, 3000)
    client.put('/users/me/notification_settings/prices', json={'enabled': False})
    db.refresh(wish)
    assert wish.alert_base_price is None

    client.put('/users/me/notification_settings/prices', json={'enabled': True})
    db.refresh(wish)
    assert wish.alert_base_price == 3000
    # Повторное включение (без смены) базу не трогает.
    wish.alert_base_price = Decimal(1)
    db.commit()
    client.put('/users/me/notification_settings/prices', json={'enabled': True})
    db.refresh(wish)
    assert wish.alert_base_price == 1


# --- POST /push/opened -----------------------------------------------------------


@pytest.fixture
def public(db: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides = {}


def test_push_opened_marks_once(public, db, user, fcm):
    wish = make_wish(db, user)
    observe(db, wish, YESTERDAY, 3000)
    observe(db, wish, TODAY, 2700)
    send_price_alerts(TODAY)
    delivery_id = fcm.messages[0].data['delivery_id']

    first = public.post('/push/opened', json={'delivery_id': delivery_id})
    assert (first.status_code, first.content) == (200, b'')
    (log,) = logs(db)
    opened_at = log.opened_at
    assert isinstance(opened_at, datetime)

    # Повтор — 200, время не двигается.
    assert (
        public.post('/push/opened', json={'delivery_id': delivery_id}).status_code
        == 200
    )
    db.refresh(log)
    assert log.opened_at == opened_at


def test_push_opened_unknown_delivery_404(public):
    response = public.post('/push/opened', json={'delivery_id': str(uuid4())})
    assert response.status_code == 404


# --- dry-run --------------------------------------------------------------------


def test_dry_run_reports_without_side_effects(db, user, fcm, availability_on):
    from app.price_alerts import dry_run_report

    wish = make_wish(db, user)
    observe(db, wish, YESTERDAY, 3000)
    observe(db, wish, TODAY, 2700)
    silent = User(
        display_name='Тихий',
        firebase_uid='silent-uid',
        firebase_push_token='token-silent',
        registered_at=utc_now(),
    )
    db.add(silent)
    db.commit()
    other = make_wish(db, silent, sku=2)
    observe(db, other, YESTERDAY, None, PriceObservationStatus.gone)
    observe(db, other, TODAY, 1490)
    set_group_enabled(db, silent, NotificationGroup.prices, False)

    report = dry_run_report(TODAY)

    assert 'юзеров 2 (из них с выключенной группой 1), строк 2' in report
    assert 'availability=1, price=1' in report
    assert "price        'Вещь 1': 3 000 ₽ → 2 700 ₽" in report
    assert "availability 'Вещь 2': — → 1 490 ₽" in report
    assert f'user {silent.id} [группа выключена]' in report
    assert 'push: „Вещь 1“ подешевела / 2 700 ₽ вместо 3 000 ₽' in report
    # Ни отправки, ни лога, ни сдвига базы.
    assert fcm.messages == [] and logs(db) == []
    db.refresh(wish)
    assert wish.alert_base_price is None
