"""Источник цены хотелки — магазин или рука (фича 0011).

Правила перехода между источниками живут здесь, а роуты лишь выбирают ветку по
телу запроса (`price_edited`, смена ссылки). Свежий запрос к магазину из
пользовательского сценария — best-effort: сбой магазина не валит сохранение.
"""

from datetime import datetime
from decimal import Decimal

import httpx
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.constants import PriceObservationStatus, PriceSource, Shop
from app.db import Wish
from app.helpers.price_watch import (
    EmptyStoreResponseError,
    ProductObservation,
    fetch_fresh_observation,
    record_fresh_observation,
)
from app.logging import logger
from app.parsers import ParsedItemInfo, parse_wildberries_link
from app.schemas import ItemInfoResponseSchema


def fetch_observation_quietly(
    link: str, client: httpx.Client
) -> ProductObservation | None:
    """Свежий запрос к магазину, сбои — тихо (`None`): превью и сохранение не
    должны падать из-за магазина, цену принесёт обход."""
    try:
        observation = fetch_fresh_observation(link, client)
    except (httpx.HTTPError, ValidationError, EmptyStoreResponseError) as error:
        logger.warning(f'Магазин не ответил на свежий запрос цены {link}: {error!r}')
        return None
    if observation is not None and observation.status != PriceObservationStatus.ok:
        # Не сбой, но цены нет — пусть по логу будет видно, почему.
        logger.info(f'Свежий запрос цены {link}: {observation.status.value}')
    return observation


def set_manual_price(wish: Wish, price: int | None) -> None:
    """Ручная цена: магазин с этого момента молчит, «от» у ручной не бывает."""
    wish.price_source = PriceSource.manual
    wish.price = Decimal(price) if price is not None else None
    wish.price_is_minimum = False


def reset_store_state(wish: Wish) -> None:
    """Ссылка сменилась — наблюдение относилось к другому товару."""
    wish.store_availability = None
    wish.store_observed_at = None


def make_store_priced(
    db: Session,
    wish: Wish,
    client: httpx.Client,
    observed_at: datetime,
    fallback_price: int | None = None,
) -> None:
    """Перевести хотелку на магазинную цену по её (новой) ссылке.

    Старая цена относилась к другому товару/источнику — сбрасываем и берём
    свежую. Магазин не ответил → `fallback_price` (на `POST` это цифра превью
    секунды назад; на `PUT` при смене ссылки фолбэка нет — в теле цена прежнего
    товара) без наблюдения и без «от». Хотелка должна быть во флеше (нужен id
    для строки истории).
    """
    wish.price_source = PriceSource.shop
    wish.price = None
    wish.price_is_minimum = False
    reset_store_state(wish)
    observation = fetch_observation_quietly(wish.link or '', client)
    if observation is None:
        wish.price = Decimal(fallback_price) if fallback_price is not None else None
        return
    record_fresh_observation(db, wish, observation, observed_at)


def build_item_info(
    parsed: ParsedItemInfo, link: str, client: httpx.Client
) -> ItemInfoResponseSchema:
    """Превью для контракта: разобранная страница + магазин и свежая цена (0011).

    Неподдерживаемый магазин — `shop = null`, цены нет. WB: цена только когда
    товар в наличии; распродано/исчез/не ответил — `price = null`, превью всё
    равно успешно.
    """
    shop = None
    price = None
    is_minimum = False
    if parse_wildberries_link(link) is not None:
        shop = Shop.wildberries
        observation = fetch_observation_quietly(link, client)
        if (
            observation is not None
            and observation.status == PriceObservationStatus.ok
            and observation.product_price is not None
        ):
            price = int(observation.product_price)
            is_minimum = observation.is_minimum
    return ItemInfoResponseSchema(
        title=parsed.title,
        description=parsed.description,
        image_url=parsed.image_url,
        shop=shop,
        price=price,
        price_is_minimum=is_minimum,
    )
