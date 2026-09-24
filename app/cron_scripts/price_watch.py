"""Обход цен и наличия по хотелкам (фича 0010), размазанный по утру.

Один тик — один запрос к WB: батч хотелок, по которым за сегодня ещё нет
наблюдения. Состояния между тиками нет, его заменяет таблица наблюдений:
упавший батч остаётся в выборке и уходит следующим тиком, рестарт
планировщика ничего не теряет. Расписание — `PRICE_WATCH_*` в `app.constants`.

Best-effort: упавший батч (сеть, 403/5xx, чужой формат ответа) — `warning`
с тем, что ответил WB, и повтор следующим тиком. `error` — в итоге перед
полуденным пушем, если за сутки не записано ни одного наблюдения: это уже бан
или смена формата, а не флуктуация. Прибор не имеет права влиять ни на что живое.
"""

import random
from datetime import date

import httpx

from app.constants import PRICE_WATCH_BATCH_SIZE
from app.db import SessionLocal
from app.helpers.browser_transport import BrowserTransport
from app.helpers.price_watch import (
    apply_observations,
    fetch_wb_cards,
    observation_row,
    observe_batch,
    save_observations,
    select_pending_targets,
    select_watch_targets,
)
from app.logging import logger
from app.utils import utc_now

# Таймаут одного запроса к магазину.
WB_REQUEST_TIMEOUT = 20
# Сколько байт тела отказа WB класть в лог: хватает на заголовок HTML-страницы.
FAILURE_BODY_LOG_BYTES = 200


def describe_failure(error: Exception) -> str:
    """Что ответил WB — для лога упавшего батча.

    На HTTP-отказ кладём метки антибота: по `status-no-id`/`x-pow` и телу
    видно, кто режет — фильтр отпечатка, бан IP или proof-of-work, а по
    голому «403» различить нельзя.
    """
    if not isinstance(error, httpx.HTTPStatusError):
        return repr(error)
    response = error.response
    body = response.content[:FAILURE_BODY_LOG_BYTES].decode(errors='replace')
    return (
        f'HTTP {response.status_code}, '
        f'status-no-id={response.headers.get("status-no-id")}, '
        f'x-pow={response.headers.get("x-pow")}, '
        f'тело={body!r}'
    )


def crawl_tick(
    client: httpx.Client,
    observed_date: date | None = None,
    batch_size: int = PRICE_WATCH_BATCH_SIZE,
    rng: random.Random | None = None,
) -> int:
    """Один тик обхода: запрос за батчем ещё не наблюдённых хотелок.

    Батч — случайная выборка из оставшихся, чтобы состав запросов не
    повторялся изо дня в день. Возвращает число записанных наблюдений.
    Клиент, дата и генератор параметризованы ради тестов без моков.
    """
    observed_at = utc_now()
    observed_date = observed_date or observed_at.date()
    with SessionLocal() as db:
        pending = select_pending_targets(db, observed_date)
    if not pending:
        return 0
    batch = (rng or random).sample(pending, min(batch_size, len(pending)))
    try:
        response = fetch_wb_cards([t.sku for t in batch], client)
        observed = observe_batch(batch, response)
        observations = [
            observation_row(target, observation, observed_date)
            for target, observation in observed
        ]
        with SessionLocal() as db:
            saved = save_observations(db, observations)
            # Фича 0011: магазинные хотелки получают цену/наличие из обхода.
            apply_observations(db, observed, observed_at)
    except Exception as error:
        # Не ошибка: батч остался невыбранным и уйдёт следующим тиком.
        logger.warning(
            f'Обход цен: батч пропущен, осталось {len(pending)}: '
            f'{describe_failure(error)}'
        )
        return 0
    logger.info(f'Обход цен: записано {saved}, осталось {len(pending) - len(batch)}')
    return saved


def report_coverage(observed_date: date | None = None) -> tuple[int, int]:
    """Итог утреннего обхода перед пушем: (наблюдено, всего целей).

    Хотелки, не наблюдённые к полудню, в сегодняшний пуш по складу не попадут.
    """
    observed_date = observed_date or utc_now().date()
    with SessionLocal() as db:
        total = len(select_watch_targets(db))
        pending = len(select_pending_targets(db, observed_date))
    observed = total - pending
    if total and not observed:
        logger.error(f'Обход цен за {observed_date} не дал ни одного наблюдения')
    else:
        logger.info(f'Обход цен за {observed_date}: наблюдено {observed} из {total}')
    return observed, total


def main() -> None:
    # С отпечатком обычного httpx WB отвечает 403 на всё — см. BrowserTransport.
    with httpx.Client(transport=BrowserTransport(timeout=WB_REQUEST_TIMEOUT)) as client:
        crawl_tick(client)


if __name__ == '__main__':
    main()
