"""Наблюдатель цен и наличия (фича 0010) — чистая часть обхода.

Обход разрезан так, чтобы всё, кроме HTTP-похода, тестировалось на сохранённом
ответе магазина без моков:

  выбор целей (`select_watch_targets`) → батчи (`batched`) →
  запрос (`fetch_wb_cards`, клиент инъецируется) → ответ в Pydantic →
  наблюдения (`build_observations`, батч + ответ) → запись (`save_observations`).

Конвертации нужен именно батч, а не только ответ: состояние «артикул исчез» — это
ОТСУТСТВИЕ товара в ответе, а отсутствие видно лишь зная, что запрашивали.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.constants import PriceObservationStatus, Shop
from app.db import Wish, WishPriceObservation
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


def _observe_product(
    target: WatchTarget, product: WbProductSchema | None
) -> tuple[PriceObservationStatus, WbPriceSchema | None]:
    """Статус и цена одной хотелки по карточке WB.

    Размер из ссылки известен → смотрим ровно его; размера в карточке больше
    нет — считаем товар исчезнувшим (то, что юзер откладывал, купить нельзя).
    Размера в ссылке нет → минимальная `product`-цена среди размеров в наличии;
    в наличии ни одного — распродан.
    """
    if product is None:
        return PriceObservationStatus.gone, None
    if target.size_option_id is not None:
        size = next(
            (s for s in product.sizes if s.option_id == target.size_option_id), None
        )
        if size is None:
            return PriceObservationStatus.gone, None
        if size.price is None:
            return PriceObservationStatus.sold_out, None
        return PriceObservationStatus.ok, size.price
    in_stock = [s.price for s in product.sizes if s.price is not None]
    if not in_stock:
        return PriceObservationStatus.sold_out, None
    return PriceObservationStatus.ok, min(in_stock, key=lambda p: p.product)


def build_observations(
    batch: Sequence[WatchTarget],
    response: WbCardResponseSchema,
    observed_date: date,
) -> list[dict]:
    """Батч + ответ WB → строки наблюдений (значения для INSERT)."""
    products = {product.id: product for product in response.products}
    observations = []
    for target in batch:
        status, price = _observe_product(target, products.get(target.sku))
        observations.append(
            {
                'wish_id': target.wish_id,
                'observed_date': observed_date,
                'shop': Shop.wildberries,
                'sku': target.sku,
                'size_option_id': target.size_option_id,
                'status': status,
                'basic_price': _kopecks_to_rubles(price.basic) if price else None,
                'product_price': _kopecks_to_rubles(price.product) if price else None,
            }
        )
    return observations


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
