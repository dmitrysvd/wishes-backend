"""Пуши по складу (фича 0013): триггеры, дайджест, база «видел», открытие.

Единственный видимый UI фичи — группа `prices` в настройках (0012); здесь —
превращение суточных наблюдений обхода (0010) в один пуш в сутки автору хотелки.
Продуктовые правила — в intent 0013 (шина); их сторона «что видит клиент»
(payload, deep link) — `x-push-payload` у `POST /push/opened`. Сами правила —
ниже, в docstring функций и тестах: кому, отбор, триггеры, дедуп, база «видел».
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import (
    PRICE_ALERT_AVAILABILITY_ENABLED,
    PRICE_ALERT_DROP_RATIO,
    PRICE_ALERT_ENABLED,
    PRICE_ALERT_NAME_MAX_LENGTH,
    PriceAlertTrigger,
    PriceObservationStatus,
    PriceSource,
)
from app.db import (
    PushReason,
    PushSendingLog,
    SessionLocal,
    User,
    UserActivityDay,
    Wish,
    WishPriceObservation,
)
from app.firebase import send_push
from app.helpers.user_helpers import get_user_deep_link
from app.logging import logger
from app.notification_settings import disabled_user_ids
from app.utils import utc_now

UNAVAILABLE = (PriceObservationStatus.sold_out, PriceObservationStatus.gone)


@dataclass(frozen=True)
class WishAlert:
    """Сработавшая хотелка: тип триггера и цены для текста пуша."""

    wish: Wish
    trigger: PriceAlertTrigger
    price: Decimal
    was_price: Decimal | None  # база «видел» — только для триггера price


def store_wishes(user: User) -> list[Wish]:
    """Хотелки, по которым фича вообще смотрит: активные, WB, цена магазина."""
    return [
        wish
        for wish in user.wishes
        if not wish.is_archived
        and wish.shop is not None
        and wish.price_source == PriceSource.shop
    ]


def _last_activity_date(db: Session, user_id: UUID) -> date | None:
    return db.scalar(
        select(func.max(UserActivityDay.activity_date)).where(
            UserActivityDay.user_id == user_id
        )
    )


def _observation_on_or_before(
    db: Session, wish_id: UUID, day: date
) -> WishPriceObservation | None:
    return db.scalars(
        select(WishPriceObservation)
        .where(
            WishPriceObservation.wish_id == wish_id,
            WishPriceObservation.observed_date <= day,
        )
        .order_by(WishPriceObservation.observed_date.desc())
        .limit(1)
    ).first()


def _first_priced_observation(
    db: Session, wish_id: UUID
) -> WishPriceObservation | None:
    return db.scalars(
        select(WishPriceObservation)
        .where(
            WishPriceObservation.wish_id == wish_id,
            WishPriceObservation.product_price.isnot(None),
        )
        .order_by(WishPriceObservation.observed_date)
        .limit(1)
    ).first()


def seen_price(db: Session, wish: Wish, last_activity: date | None) -> Decimal | None:
    """База «видел» без участия клиента.

    Что позже: сохранённая база (пуш / кнопка / включение группы) либо цена
    наблюдения на последний день активности юзера (карточка показывала его).
    Ни того ни другого — цена, с которой хотелка началась (первое наблюдение:
    оно пишется при сохранении с WB-ссылкой). Наблюдение без цены
    (распродано/нет) базой быть не может.
    """
    base_price, base_at = wish.alert_base_price, wish.alert_base_at
    activity_obs = (
        _observation_on_or_before(db, wish.id, last_activity)
        if last_activity is not None
        else None
    )
    if activity_obs is not None and activity_obs.product_price is None:
        activity_obs = None
    if base_price is not None and base_at is not None:
        if activity_obs is None or activity_obs.observed_date <= base_at.date():
            return base_price
    if activity_obs is not None:
        return activity_obs.product_price
    first = _first_priced_observation(db, wish.id)
    return first.product_price if first is not None else None


def _latest_two(
    db: Session, wish_id: UUID
) -> tuple[WishPriceObservation | None, WishPriceObservation | None]:
    rows = db.scalars(
        select(WishPriceObservation)
        .where(WishPriceObservation.wish_id == wish_id)
        .order_by(WishPriceObservation.observed_date.desc())
        .limit(2)
    ).all()
    latest = rows[0] if rows else None
    previous = rows[1] if len(rows) > 1 else None
    return latest, previous


def detect_alert(
    db: Session, wish: Wish, today: date, last_activity: date | None
) -> WishAlert | None:
    """Сработала ли хотелка по сегодняшнему наблюдению обхода.

    Только по наблюдению за `today`: обход упал — событий нет, задним числом не
    догоняем. «Снова в наличии» — предыдущее наблюдение распродано/нет; иначе
    «подешевело» — цена ниже базы «видел» на ≥ порога. Рост цены — молчим.
    """
    latest, previous = _latest_two(db, wish.id)
    if (
        latest is None
        or latest.observed_date != today
        or latest.status != PriceObservationStatus.ok
        or latest.product_price is None
    ):
        return None
    price = latest.product_price
    if previous is not None and previous.status in UNAVAILABLE:
        # Возврат в наличие: событие само по себе, без сравнения с базой. Пока
        # триггер выключен — молчим (сравнивать с базой до распродажи не по чему).
        if not PRICE_ALERT_AVAILABILITY_ENABLED:
            return None
        return WishAlert(wish, PriceAlertTrigger.availability, price, None)
    # База есть всегда: сегодняшнее наблюдение с ценой — уже кандидат в «первое».
    base = seen_price(db, wish, last_activity)
    assert base is not None
    if price <= base * (1 - PRICE_ALERT_DROP_RATIO):
        return WishAlert(wish, PriceAlertTrigger.price, price, base)
    return None


def rubles(value: Decimal) -> str:
    """`2700` → `2 700 ₽`: целые рубли, пробел-разделитель тысяч (контракт)."""
    whole = int(value.to_integral_value(rounding=ROUND_DOWN))
    return f'{whole:,}'.replace(',', ' ') + ' ₽'


def _things(count: int) -> str:
    """«2 вещи», «5 вещей», «21 вещь» — склонение по последним цифрам."""
    tail, tail2 = count % 10, count % 100
    if tail == 1 and tail2 != 11:
        word = 'вещь'
    elif 2 <= tail <= 4 and not 12 <= tail2 <= 14:
        word = 'вещи'
    else:
        word = 'вещей'
    return f'{count} {word}'


def short_name(name: str) -> str:
    """Название для текста пуша: не длиннее лимита, хвост — многоточие."""
    if len(name) <= PRICE_ALERT_NAME_MAX_LENGTH:
        return name
    return name[: PRICE_ALERT_NAME_MAX_LENGTH - 1].rstrip() + '…'


def _one_line(alert: WishAlert) -> tuple[str, str]:
    """(заголовок, тело) одной хотелки — тексты из контракта."""
    name = f'„{short_name(alert.wish.name)}“'
    if alert.trigger == PriceAlertTrigger.availability:
        return f'{name} снова в наличии', f'{rubles(alert.price)} на WB'
    assert alert.was_price is not None
    return (
        f'{name} подешевела',
        f'{rubles(alert.price)} вместо {rubles(alert.was_price)}',
    )


def build_message(
    user: User, alerts: list[WishAlert]
) -> tuple[str, str, str, PriceAlertTrigger]:
    """(title, body, link, trigger) дайджеста по контракту `POST /push/opened`."""
    first = alerts[0]
    if len(alerts) == 1:
        title, body = _one_line(first)
        link = f'{settings.FRONTEND_URL}/wish?wishId={first.wish.id}'
        return title, body, link, first.trigger
    triggers = {alert.trigger for alert in alerts}
    trigger = triggers.pop() if len(triggers) == 1 else PriceAlertTrigger.mixed
    _, first_body = _one_line(first)
    # Строка первой хотелки: «„Название“: 2 700 ₽ вместо 3 000 ₽».
    body = f'„{short_name(first.wish.name)}“: {first_body}'
    title = f'{_things(len(alerts))} из списка подешевели или вернулись в наличие'
    return title, body, get_user_deep_link(user), trigger


def already_sent_today(db: Session, user_id: UUID, today: date) -> bool:
    """Один пуш по складу в календарные сутки UTC — по логу отправок.

    `sent_at` в логе — naive `datetime.now()` (сервер живёт в UTC), поэтому и
    граница суток naive."""
    day_start = datetime.combine(today, datetime.min.time())
    return (
        db.scalars(
            select(PushSendingLog.id).where(
                PushSendingLog.reason == PushReason.PRICE_ALERT,
                PushSendingLog.target_user_id == user_id,
                PushSendingLog.sent_at >= day_start,
            )
        ).first()
        is not None
    )


@dataclass(frozen=True)
class UserDigest:
    """Что ушло бы юзеру сегодня: сработавшие хотелки и собранный текст."""

    user: User
    alerts: list[WishAlert]
    title: str
    body: str
    link: str
    trigger: PriceAlertTrigger


def collect_digests(today: date) -> list[UserDigest]:
    """Чтение без побочных эффектов: кому и что ушло бы за `today`.

    Общая часть крона и dry-run. Дедуп «один в сутки» — здесь (по логу);
    выключенная группа — нет: её отсекает `send_push`, а dry-run помечает.
    """
    with SessionLocal() as db:
        users = db.scalars(select(User).where(User.can_receive_push))
        candidates = [(user, store_wishes(user)) for user in users]
    digests: list[UserDigest] = []
    for user, wishes in candidates:
        if not wishes:
            continue
        with SessionLocal() as db:
            if already_sent_today(db, user.id, today):
                continue
            last_activity = _last_activity_date(db, user.id)
            alerts = [
                alert
                for wish in wishes
                if (alert := detect_alert(db, wish, today, last_activity))
            ]
        if not alerts:
            continue
        title, body, link, trigger = build_message(user, alerts)
        digests.append(UserDigest(user, alerts, title, body, link, trigger))
    return digests


def send_price_alerts(today: date | None = None) -> int:
    """Крон в полдень после суточного обхода: дайджест по складу каждому автору.

    База «видел» сдвигается на цену из пуша только если FCM принял сообщение;
    выключенная группа `prices` отсекается в `send_push` — базу не двигает и
    события не копит. Возвращает число отправленных пушей.
    """
    if not PRICE_ALERT_ENABLED:
        logger.info('Пуши по складу выключены (PRICE_ALERT_ENABLED) — пропуск')
        return 0
    today = today or utc_now().date()
    sent_total = 0
    for digest in collect_digests(today):
        user = digest.user
        outcome = send_push(
            [user],
            digest.title,
            digest.body,
            reason=PushReason.PRICE_ALERT,
            link=digest.link,
            trigger=digest.trigger,
            with_delivery_id=True,
        )
        sent_total += outcome.sent
        if user.id not in outcome.accepted_user_ids:
            continue
        now = utc_now()
        with SessionLocal() as db:
            for alert in digest.alerts:
                wish = db.get(Wish, alert.wish.id)
                if wish is not None:
                    wish.alert_base_price = alert.price
                    wish.alert_base_at = now
            db.commit()
    logger.info(f'Пуши по складу: отправлено {sent_total}')
    return sent_total


def dry_run_report(today: date | None = None) -> str:
    """Сухой прогон: что ушло бы, без отправки, лога и сдвига базы.

    Персональных данных сверх названий хотелок нет: юзер — только id. Юзеры с
    выключенной группой показаны с пометкой — реальный крон их пропустит.
    """
    today = today or utc_now().date()
    digests = collect_digests(today)
    with SessionLocal() as db:
        opted_out = disabled_user_ids(
            db, (d.user.id for d in digests), PushReason.PRICE_ALERT
        )
    lines = [
        f'dry-run за {today}: юзеров {len(digests)} '
        f'(из них с выключенной группой {len(opted_out)}), '
        f'строк {sum(len(d.alerts) for d in digests)}',
    ]
    by_trigger: dict[str, int] = {}
    for digest in digests:
        by_trigger[digest.trigger.value] = by_trigger.get(digest.trigger.value, 0) + 1
    lines.append(
        'по типу пуша: ' + ', '.join(f'{k}={v}' for k, v in sorted(by_trigger.items()))
    )
    for digest in digests:
        flag = ' [группа выключена]' if digest.user.id in opted_out else ''
        lines.append(f'--- user {digest.user.id}{flag} → {digest.trigger.value}')
        for alert in digest.alerts:
            was = rubles(alert.was_price) if alert.was_price is not None else '—'
            lines.append(
                f'  {alert.trigger.value:<12} {alert.wish.name!r}: '
                f'{was} → {rubles(alert.price)}'
            )
        lines.append(f'  push: {digest.title} / {digest.body}')
    return '\n'.join(lines)


def mark_seen(wish: Wish, price: Decimal | None, at: datetime) -> None:
    """Юзер «видел» цену: кнопка «актуальная с WB» / включение группы.
    Без цены (распродано) база не двигается."""
    if price is None:
        return
    wish.alert_base_price = price
    wish.alert_base_at = at


def reset_seen_for_user(db: Session, user: User) -> None:
    """Включение группы `prices`: база = текущая цена карточки у всех активных
    магазинных хотелок — события за время «выключено» не копятся."""
    now = utc_now()
    for wish in store_wishes(user):
        mark_seen(wish, wish.price, now)
    db.commit()


def register_push_open(db: Session, delivery_id: UUID) -> bool:
    """Открытие по пушу: первое — фиксируем, повтор — no-op. False — доставки нет."""
    log = db.get(PushSendingLog, delivery_id)
    if log is None:
        return False
    if log.opened_at is None:
        log.opened_at = utc_now()
        db.commit()
    return True


if __name__ == '__main__':  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description='Пуши по складу (фича 0013)')
    parser.add_argument(
        '--dry-run', action='store_true', help='посчитать и напечатать, не отправлять'
    )
    parser.add_argument('--date', type=date.fromisoformat, default=None)
    args = parser.parse_args()
    if args.dry_run:
        print(dry_run_report(args.date))
    else:
        send_price_alerts(args.date)
