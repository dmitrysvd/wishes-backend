"""Наблюдатель цен и наличия (фича 0010) — чистая часть обхода.

Обход разрезан так, чтобы всё, кроме HTTP-похода, тестировалось на сохранённом
ответе магазина без моков:

  выбор целей (`select_watch_targets`) → батчи (`batched`) →
  запрос (`fetch_wb_cards`, клиент инъецируется) → ответ в Pydantic →
  наблюдения (`observe_batch`, батч + ответ) → запись истории
  (`save_observations`) → обновление магазинных хотелок (`apply_observations`).

Конвертации нужен именно батч, а не только ответ: состояние «артикул исчез» — это
ОТСУТСТВИЕ товара в ответе, а отсутствие видно лишь зная, что запрашивали.

Та же конвертация обслуживает свежий запрос по одной ссылке из пользовательского
сценария (фича 0011: превью, сохранение, кнопка) — `fetch_fresh_observation`.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal
from uuid import UUID

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.constants import (
    STORE_REQUEST_TIMEOUT_SECONDS,
    PriceObservationStatus,
    PriceSource,
    Shop,
)
from app.db import Wish, WishPriceObservation
from app.helpers.browser_transport import BrowserTransport
from app.parsers import parse_wildberries_link

# Публичный батчевый эндпоинт карточек WB. `dest` — регион (влияет на наличие и
# цену), `curr=rub` — цены в копейках.
WB_CARD_API_URL = 'https://card.wb.ru/cards/v4/detail'
WB_CARD_API_PARAMS = {'appType': '1', 'curr': 'rub', 'dest': '-1257786'}
# WB отдаёт цены целыми копейками.
KOPECKS_IN_RUBLE = 100


@dataclass(frozen=True)
class WatchTarget:
    """Одна хотелка в обходе: что запросить у магазина и куда записать ответ."""

    wish_id: UUID
    sku: int
    size_option_id: int | None


class WbPriceSchema(BaseModel):
    basic: int
    product: int


class WbSizeSchema(BaseModel):
    option_id: int = Field(alias='optionId')
    # Нет `price` — размер распродан.
    price: WbPriceSchema | None = None


class WbProductSchema(BaseModel):
    id: int
    sizes: list[WbSizeSchema]


class WbCardResponseSchema(BaseModel):
    """Ответ card.wb.ru: только то, что нужно наблюдению. Смена формата у WB
    превращается в `ValidationError` здесь, а не в `KeyError` в конвертации."""

    products: list[WbProductSchema]


def select_watch_targets(db: Session) -> list[WatchTarget]:
    """Активные хотелки со ссылкой на WB, из которой читается артикул.

    Хотелка без ссылки или на другой магазин просто не попадает в обход —
    это норма, не ошибка. Архивные выбывают (🟡 Q2 intent'а).
    """
    wishes = db.execute(
        select(Wish.id, Wish.link).where(Wish.link.isnot(None), ~Wish.is_archived)
    ).all()
    targets = []
    for wish_id, link in wishes:
        parsed = parse_wildberries_link(link)
        if parsed is None:
            continue
        targets.append(WatchTarget(wish_id, *parsed))
    return targets


def batched(targets: Sequence[WatchTarget], size: int) -> Iterator[list[WatchTarget]]:
    for start in range(0, len(targets), size):
        yield list(targets[start : start + size])


def fetch_wb_cards(skus: Sequence[int], client: httpx.Client) -> WbCardResponseSchema:
    """Один запрос к WB за батчем артикулов. Единственная сетевая функция обхода."""
    response = client.get(
        WB_CARD_API_URL,
        params={**WB_CARD_API_PARAMS, 'nm': ';'.join(str(sku) for sku in skus)},
    )
    response.raise_for_status()
    return WbCardResponseSchema.model_validate(response.json())


def _kopecks_to_rubles(kopecks: int) -> Decimal:
    return Decimal(kopecks) / KOPECKS_IN_RUBLE


@dataclass(frozen=True)
class ProductObservation:
    """Результат одного наблюдения: статус, цена (только при `ok`) и признак
    «цена — минимум среди размеров» (ссылка без размера у многоразмерного
    товара; UI рисует «от»)."""

    status: PriceObservationStatus
    price: WbPriceSchema | None = None
    is_minimum: bool = False

    @property
    def product_price(self) -> Decimal | None:
        return _kopecks_to_rubles(self.price.product) if self.price else None

    @property
    def basic_price(self) -> Decimal | None:
        return _kopecks_to_rubles(self.price.basic) if self.price else None


def observe_product(
    size_option_id: int | None, product: WbProductSchema | None
) -> ProductObservation:
    """Статус и цена товара по карточке WB.

    Размер из ссылки известен → смотрим ровно его; размера в карточке больше
    нет — считаем товар исчезнувшим (то, что юзер откладывал, купить нельзя).
    Размера в ссылке нет → минимальная `product`-цена среди размеров в наличии;
    в наличии ни одного — распродан.
    """
    if product is None:
        return ProductObservation(PriceObservationStatus.gone)
    if size_option_id is not None:
        size = next((s for s in product.sizes if s.option_id == size_option_id), None)
        if size is None:
            return ProductObservation(PriceObservationStatus.gone)
        if size.price is None:
            return ProductObservation(PriceObservationStatus.sold_out)
        return ProductObservation(PriceObservationStatus.ok, size.price)
    in_stock = [s.price for s in product.sizes if s.price is not None]
    if not in_stock:
        return ProductObservation(PriceObservationStatus.sold_out)
    return ProductObservation(
        PriceObservationStatus.ok,
        min(in_stock, key=lambda p: p.product),
        is_minimum=len(product.sizes) > 1,
    )


def observe_batch(
    batch: Sequence[WatchTarget], response: WbCardResponseSchema
) -> list[tuple[WatchTarget, ProductObservation]]:
    """Батч + ответ WB → наблюдение по каждой цели (отсутствие в ответе = gone)."""
    products = {product.id: product for product in response.products}
    return [
        (target, observe_product(target.size_option_id, products.get(target.sku)))
        for target in batch
    ]


def observation_row(
    target: WatchTarget, observation: ProductObservation, observed_date: date
) -> dict:
    """Строка истории наблюдений (значения для INSERT)."""
    return {
        'wish_id': target.wish_id,
        'observed_date': observed_date,
        'shop': Shop.wildberries,
        'sku': target.sku,
        'size_option_id': target.size_option_id,
        'status': observation.status,
        'basic_price': observation.basic_price,
        'product_price': observation.product_price,
    }


def build_observations(
    batch: Sequence[WatchTarget],
    response: WbCardResponseSchema,
    observed_date: date,
) -> list[dict]:
    """Батч + ответ WB → строки наблюдений (значения для INSERT)."""
    return [
        observation_row(target, observation, observed_date)
        for target, observation in observe_batch(batch, response)
    ]


def sync_wish_with_store(
    wish: Wish, observation: ProductObservation, observed_at: datetime
) -> None:
    """Применить наблюдение к магазинной хотелке.

    Наличие и давность — всегда; цена и «от» — только когда товар в наличии:
    магазин цену никогда не обнуляет, при распродано/исчез остаётся последняя
    известная (решение продукта, intent 0011).
    """
    wish.store_availability = observation.status
    wish.store_observed_at = observed_at
    if observation.status == PriceObservationStatus.ok and observation.product_price:
        # Контракт отдаёт цену целым числом рублей, копейки отбрасываются; в
        # истории наблюдений цена остаётся точной.
        wish.price = observation.product_price.to_integral_value(rounding=ROUND_DOWN)
        wish.price_is_minimum = observation.is_minimum


def apply_observations(
    db: Session,
    observed: Sequence[tuple[WatchTarget, ProductObservation]],
    observed_at: datetime,
) -> int:
    """Обновить хотелки батча с магазинной ценой; возвращает число обновлённых.

    Ручные (`manual`) не трогаем — юзер поменял цену, магазин для него молчит;
    история наблюдений по ним всё равно пишется (прибор 0010).
    """
    by_wish_id = {target.wish_id: observation for target, observation in observed}
    wishes = db.scalars(
        select(Wish).where(
            Wish.id.in_(by_wish_id), Wish.price_source == PriceSource.shop
        )
    ).all()
    for wish in wishes:
        sync_wish_with_store(wish, by_wish_id[wish.id], observed_at)
    db.commit()
    return len(wishes)


def fetch_fresh_observation(
    link: str, client: httpx.Client
) -> ProductObservation | None:
    """Свежий запрос к магазину по одной ссылке (фича 0011).

    None — ссылка не на поддерживаемый магазин (спрашивать нечего). Сетевые
    ошибки и чужой формат ответа идут наверх (`httpx.HTTPError`,
    `pydantic.ValidationError`) — вызывающий решает, тихо это или `502`.
    """
    parsed = parse_wildberries_link(link)
    if parsed is None:
        return None
    sku, size_option_id = parsed
    response = fetch_wb_cards([sku], client)
    products = {product.id: product for product in response.products}
    return observe_product(size_option_id, products.get(sku))


def record_fresh_observation(
    db: Session, wish: Wish, observation: ProductObservation, observed_at: datetime
) -> None:
    """Свежее наблюдение из пользовательского сценария: обновить хотелку и
    дописать историю (если за эти сутки строки ещё нет — первое наблюдение за
    сутки остаётся истиной прибора). Коммит — на вызывающем."""
    sync_wish_with_store(wish, observation, observed_at)
    parsed = parse_wildberries_link(wish.link or '')
    assert parsed is not None, 'наблюдение бывает только у WB-ссылки'
    target = WatchTarget(wish.id, *parsed)
    db.execute(
        pg_insert(WishPriceObservation)
        .values([observation_row(target, observation, observed_at.date())])
        .on_conflict_do_nothing(index_elements=['wish_id', 'observed_date'])
    )


def save_observations(db: Session, observations: Sequence[dict]) -> int:
    """Записать батч наблюдений; возвращает число реально вставленных строк.

    `ON CONFLICT DO NOTHING` по `(wish_id, observed_date)`: первое наблюдение за
    сутки — истина, повторный запуск в те же сутки не перетирает его, а лишь
    дозаполняет хотелки, чей батч днём упал (бесплатный ретрай).
    """
    if not observations:
        return 0
    result = db.execute(
        pg_insert(WishPriceObservation)
        .values(list(observations))
        .on_conflict_do_nothing(index_elements=['wish_id', 'observed_date'])
        .returning(WishPriceObservation.wish_id)
    )
    inserted = len(result.all())
    db.commit()
    return inserted


def get_store_client() -> Iterator[httpx.Client]:
    """FastAPI-зависимость: клиент для свежего запроса цены к магазину (0011).

    Зависимость, а не глобальный объект: тесты подменяют её клиентом на
    `httpx.MockTransport` через `app.dependency_overrides` — без моков внутри
    логики. С отпечатком обычного httpx WB отвечает 403 — см. BrowserTransport.
    Таймаут — бюджет, обещанный контрактом (не дольше 10 с).
    """
    with httpx.Client(
        transport=BrowserTransport(timeout=STORE_REQUEST_TIMEOUT_SECONDS)
    ) as client:
        yield client
