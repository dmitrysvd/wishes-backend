"""Суточный обход цен и наличия по хотелкам (фича 0010).

Best-effort: упавший батч (сеть, 5xx, чужой формат ответа) — пропущенное
наблюдение и `warning`, обход идёт дальше; коммит по батчам, чтобы удачные не
пропали из-за неудачных. `error` — только если весь обход не дал ни одного
наблюдения: это уже смена формата или бан, а не флуктуация.
Прибор не имеет права влиять ни на что живое.
"""

import time
from datetime import date

import httpx

from app.constants import PRICE_WATCH_BATCH_PAUSE_SECONDS, PRICE_WATCH_BATCH_SIZE
from app.db import SessionLocal
from app.helpers.browser_transport import BrowserTransport
from app.helpers.price_watch import (
    batched,
    build_observations,
    fetch_wb_cards,
    save_observations,
    select_watch_targets,
)
from app.logging import logger
from app.utils import utc_now

# Таймаут одного запроса к магазину.
WB_REQUEST_TIMEOUT = 20


def crawl(
    client: httpx.Client,
    observed_date: date | None = None,
    batch_size: int = PRICE_WATCH_BATCH_SIZE,
    pause_seconds: float = PRICE_WATCH_BATCH_PAUSE_SECONDS,
) -> int:
    """Обойти все цели одним проходом; возвращает число записанных наблюдений.

    Клиент, дата и пауза параметризованы ради тестируемости без моков
    (`httpx.MockTransport`, фиксированная дата, нулевая пауза).
    """
    observed_date = observed_date or utc_now().date()
    with SessionLocal() as db:
        targets = select_watch_targets(db)
    logger.info(f'Обход цен: {len(targets)} хотелок, дата {observed_date}')
    saved = 0
    failed_batches = 0
    for index, batch in enumerate(batched(targets, batch_size)):
        if index:
            time.sleep(pause_seconds)
        try:
            response = fetch_wb_cards([t.sku for t in batch], client)
            observations = build_observations(batch, response, observed_date)
            with SessionLocal() as db:
                saved += save_observations(db, observations)
        except Exception as error:
            # Пропущенное наблюдение, не ошибка: дырка в истории допустима.
            failed_batches += 1
            logger.warning(f'Обход цен: батч #{index} пропущен: {error!r}')
    if targets and not saved and failed_batches:
        logger.error(
            f'Обход цен не дал ни одного наблюдения: {failed_batches} батчей упало'
        )
    logger.info(f'Обход цен: записано {saved}, батчей упало {failed_batches}')
    return saved


def main() -> None:
    # С отпечатком обычного httpx WB отвечает 403 на всё — см. BrowserTransport.
    with httpx.Client(transport=BrowserTransport(timeout=WB_REQUEST_TIMEOUT)) as client:
        crawl(client)


if __name__ == '__main__':
    main()
