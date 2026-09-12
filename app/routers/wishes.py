from hashlib import md5
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.status import (
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_502_BAD_GATEWAY,
)

from app.config import settings
from app.constants import PriceRefreshOutcome, PriceSource
from app.db import User, Wish, WishPriceRefreshEvent, WishRecommendation
from app.dependencies import (
    WISHES_TAG,
    get_current_user,
    get_current_user_wish,
    get_db,
    get_store_client,
)
from app.helpers import IMAGE_UPLOAD_RESPONSES, read_uploaded_image
from app.helpers.price_watch import (
    fetch_fresh_observation,
    record_fresh_observation,
)
from app.helpers.store_price import (
    make_store_priced,
    reset_store_state,
    set_manual_price,
)
from app.logging import logger
from app.parsers import parse_wildberries_link
from app.schemas import WishReadSchema, WishWriteSchema
from app.utils import utc_now

router = APIRouter(tags=[WISHES_TAG])

# Общие коды ответов защищённых роутов хотелки (контракт, фича 0011).
_AUTH_RESPONSE: dict[int | str, dict[str, Any]] = {
    HTTP_401_UNAUTHORIZED: {
        'description': 'Нет или истёк токен авторизации.',
        'content': {'application/json': {'example': {'detail': 'Not authenticated'}}},
    },
}
_OWNER_RESPONSES: dict[int | str, dict[str, Any]] = {
    **_AUTH_RESPONSE,
    HTTP_403_FORBIDDEN: {
        'description': (
            'Хотелка чужая: менять её (в т.ч. цену и источник) может только автор.'
        ),
        'content': {'application/json': {'example': {'detail': 'Forbidden'}}},
    },
    HTTP_404_NOT_FOUND: {
        'description': 'Хотелки с таким `wish_id` нет (удалена).',
        'content': {'application/json': {'example': {'detail': 'Not Found'}}},
    },
}
_STORE_PRICE_ON_CREATE = (
    '**Цена с магазина при создании (фича 0011).** Если `link` — ссылка на '
    'поддерживаемый магазин (только Wildberries) и `price_edited = false`, цена у '
    'хотелки магазинная '
    '(`price_source = shop`): бэк делает свежий запрос к магазину и пишет наблюдение '
    '(`store_observation`); `price` из тела игнорируется. Результат запроса: товар в '
    'наличии → `price` = текущая цена со скидкой (`price_is_minimum` = true, если это '
    'минимум среди размеров при ссылке без `?size=`); распродан/исчез → '
    '`store_observation.availability` = `sold_out`/`gone`, `price = null` (цены не '
    'было никогда); магазин не ответил за 10 с → `store_observation = null`, а '
    '`price` = число из тела как фолбэк (это цифра превью секунды назад; '
    '`price_is_minimum = false`), null в теле → `price = null`. Всё это тихо, '
    'сохранение не падает. '
    'Если `price_edited = true` — цена ручная (`manual`), `price` из тела сохраняется '
    'как есть (null = без цены), магазин не опрашивается. Ссылки на поддерживаемый '
    'магазин нет → `price` из тела сохраняется как ручная независимо от флага.'
)

# Константы
BASE_DIR = Path(__file__).parent.parent.parent
WISH_IMAGES_DIR = settings.MEDIA_ROOT / 'wish_images'

# Описание POST собирается из общего куска про магазинную цену, поэтому это
# f-строка в `description=`, а не докстринг (f-строка докстрингом не станет).
_ADD_WISH_DESCRIPTION = f"""Добавить хотелку.

При `recommendation_id` бэк сам копирует картинку рекомендации в хотелку.

{_STORE_PRICE_ON_CREATE}

Типичный путь «по ссылке»: клиент показал превью (`POST /item_info_from_page`),
поле цены заполнилось магазинной ценой, юзер её не трогал → `price_edited = false`,
`price` — эхо превью (бэк берёт свежую цену сам, эхо — лишь фолбэк). Сбои:
- WB ответил при сохранении → его цена, эхо не используется;
- WB не ответил при сохранении, превью цену дало → `price` = цифра из тела,
  `price_source = shop`, `store_observation = null`, `price_is_minimum = false`
  (юзер видит в карточке то, что видел в форме; «от» из превью при этом теряется
  до первого наблюдения — обход поправит и цену, и флаг);
- WB не ответил ни на превью, ни при сохранении (или превью упало `400`) →
  `price = null`, `price_source = shop`, `store_observation = null`: цену принесёт
  суточный обход. Источник решает не превью, а WB-ссылка + `price_edited`.

**Длительность:** запрос к магазину при сохранении — не дольше 10 с (потом
сохраняем без цены); клиентский таймаут на `POST`/`PUT` — от 15 с.
"""


@router.post(
    '/wishes',
    response_model=WishReadSchema,
    description=_ADD_WISH_DESCRIPTION,
    responses={
        200: {
            'description': (
                'Хотелка создана; в ответе её итоговое состояние, включая `price`, '
                '`price_source`, `shop`, `store_observation` — отдельный GET не нужен.'
            )
        },
        **_AUTH_RESPONSE,
        HTTP_404_NOT_FOUND: {
            'description': '`recommendation_id` указан, но такой рекомендации нет.',
            'content': {
                'application/json': {'example': {'detail': 'Recommendation not found'}}
            },
        },
    },
)
def add_wish(
    wish_data: WishWriteSchema,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    store_client: httpx.Client = Depends(get_store_client),
):
    recommendation_id = None
    if wish_data.recommendation_id:
        rec = db.scalars(
            select(WishRecommendation).where(
                WishRecommendation.id == wish_data.recommendation_id
            )
        ).one_or_none()
        if not rec:
            raise HTTPException(HTTP_404_NOT_FOUND, 'Recommendation not found')
        recommendation_id = rec.id

    link = str(wish_data.link) if wish_data.link else None
    wish = Wish(
        user_id=user.id,
        name=wish_data.name,
        description=wish_data.description,
        link=link,
        recommendation_id=recommendation_id,
    )
    db.add(wish)
    if _is_store_link(link) and not wish_data.price_edited:
        db.flush()  # id хотелки нужен строке истории наблюдений
        make_store_priced(
            db, wish, store_client, utc_now(), fallback_price=wish_data.price
        )
    else:
        set_manual_price(wish, wish_data.price)
    db.commit()
    return wish


def _is_store_link(link: str | None) -> bool:
    return link is not None and parse_wildberries_link(link) is not None


@router.get(
    '/wishes',
    response_model=list[WishReadSchema],
    responses={
        200: {
            'description': (
                'Активные (не архивные) хотелки текущего юзера целиком, без пагинации. '
                'Пустой список — желаний нет (не ошибка).'
            )
        },
        **_AUTH_RESPONSE,
    },
)
def my_wishes(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Мои активные хотелки — список автора (плашки магазина/наличия показываются
    здесь и в карточке; см. `WishReadSchema`)."""
    query = Wish.get_active_wish_query().where(Wish.user == user)
    return db.scalars(query)


@router.get('/reserved_wishes', response_model=list[WishReadSchema])
def my_reserved_wishes(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    query = Wish.get_active_wish_query().where(Wish.reserved_by == user)
    return db.scalars(query)


@router.get(
    '/wishes/{wish_id}',
    response_model=WishReadSchema,
    responses={
        200: {'description': 'Карточка хотелки (своей или чужой активной).'},
        **_AUTH_RESPONSE,
        HTTP_404_NOT_FOUND: {
            'description': 'Хотелки нет, либо она в архиве у другого юзера.',
            'content': {'application/json': {'example': {'detail': 'Wish not found'}}},
        },
    },
)
def get_wish(
    wish_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Карточка хотелки. Автор видит и архивные; чужие — только активные.

    Что из магазинных полей показывать автору, а что чужим — см. `WishReadSchema`.
    """
    wish = db.scalars(select(Wish).where(Wish.id == wish_id)).one_or_none()
    if not wish or (wish.is_archived and wish.user != user):
        raise HTTPException(HTTP_404_NOT_FOUND, 'Wish not found')
    return wish


@router.put(
    '/wishes/{wish_id}',
    response_model=WishReadSchema,
    responses={
        200: {
            'description': (
                'Сохранено; в ответе итоговое состояние хотелки (`price`, '
                '`price_source`, `shop`, `store_observation`) — форма обновляется '
                'из него без отдельного GET.'
            )
        },
        **_OWNER_RESPONSES,
    },
)
def update_wish(
    wish_data: WishWriteSchema,
    db: Session = Depends(get_db),
    wish: Wish = Depends(get_current_user_wish),
    store_client: httpx.Client = Depends(get_store_client),
):
    """Сохранить форму хотелки целиком (name, description, link,
    price).

    `recommendation_id` в теле игнорируется. Картинка этим запросом не меняется
    (см. `/wishes/{wish_id}/image`).

    **Цена: ручная правка vs эхо формы.** Решает только `price_edited` (см.
    `WishWriteSchema`), значения не сравниваются:
    - `price_edited = true` → `price` из тела сохраняется как ручная цена
      (null = стёр), `price_source = manual`, `store_observation = null`,
      `price_is_minimum = false`; магазин перестаёт обновлять цену. Это так, даже
      если цифра совпала с магазинной.
    - `price_edited = false` и `price_source` уже `shop` → `price` из тела
      игнорируется, магазинная цена и наблюдение остаются как есть (обход мог
      обновить цену между загрузкой формы и сохранением — это не правка).
    - `price_edited = false` и `price_source = manual` → `price` из тела сохраняется
      (для нового клиента это эхо текущего значения, для старого — единственный способ
      задать цену).

    **Смена ссылки** (строка `link` отличается от сохранённой; одинаковая строка —
    ссылка не менялась, ничего ниже не происходит):
    - новая ссылка на поддерживаемый магазин и `price_edited = false` → старая цена
      относилась к другому товару: `price_source = shop`, свежий запрос к магазину;
      в наличии → `price` = текущая; распродан/исчез/магазин недоступен →
      `price = null` (до первого наблюдения с ценой), `store_observation` — по
      результату (`sold_out`/`gone` или null при недоступности);
    - новая ссылка на поддерживаемый магазин и `price_edited = true` → ручная
      побеждает: `price_source = manual`, `price` из тела;
    - новая ссылка на неподдерживаемый магазин или `link = null` →
      `price_source = manual`, `shop = null`, `store_observation = null`,
      `price_is_minimum = false`; `price`: при `price_edited = true` — из тела,
      иначе остаётся прежняя цифра (что было — остаётся, но больше не
      обновляется; «от» при этом снимается — ручная цена минимумом не бывает).
    В отличие от `POST`, фолбэка на `price` из тела при недоступном магазине здесь
    нет: в теле — цена прежнего товара, а не превью нового.

    **Ссылка не менялась.** Магазин НЕ опрашивается: цена и наблюдение меняются только
    обходом (при `shop`) или ручной правкой (`price_edited = true`). Вернуть магазинную
    цену у ручной хотелки — `POST /wishes/{wish_id}/refresh_store_price`, не PUT.
    """
    new_link = str(wish_data.link) if wish_data.link else None
    link_changed = new_link != wish.link
    wish.name = wish_data.name
    wish.description = wish_data.description
    wish.link = new_link
    if wish_data.price_edited:
        # Осознанная правка побеждает всё, включая смену ссылки.
        set_manual_price(wish, wish_data.price)
        if link_changed:
            reset_store_state(wish)
    elif link_changed:
        if _is_store_link(new_link):
            # Старая цена относилась к другому товару: фолбэка на тело нет.
            make_store_priced(db, wish, store_client, utc_now())
        else:
            # Что было — остаётся, но больше не обновляется; «от» снимается.
            wish.price_source = PriceSource.manual
            wish.price_is_minimum = False
            reset_store_state(wish)
    elif wish.price_source == PriceSource.manual:
        # Эхо текущего значения для нового клиента, единственный способ задать
        # цену — для старого. У `shop` тело игнорируется: эхо могло устареть.
        set_manual_price(wish, wish_data.price)
    db.add(wish)
    db.commit()
    return wish


@router.post(
    '/wishes/{wish_id}/refresh_store_price',
    response_model=WishReadSchema,
    responses={
        200: {
            'description': (
                'Магазин ответил: `price_source = shop`, `store_observation` свежее '
                '(`observed_at` = сейчас). В наличии → `price` = текущая цена со '
                'скидкой, `price_is_minimum` пересчитан (true — ссылка без `?size=` '
                'и это минимум среди размеров); распродан/исчез → `availability` = '
                '`sold_out`/`gone`, `price` и `price_is_minimum` не меняются '
                '(последняя известная остаётся; у бывшей ручной — ручная цифра с '
                '`price_is_minimum = false`).'
            )
        },
        **_OWNER_RESPONSES,
        HTTP_409_CONFLICT: {
            'description': (
                'У хотелки нет ссылки на поддерживаемый магазин (`shop = null`): '
                'актуализировать нечего. Кнопку в этом состоянии не показывайте.'
            ),
            'content': {
                'application/json': {
                    'example': {'detail': 'Ссылка не на поддерживаемый магазин'}
                }
            },
        },
        HTTP_502_BAD_GATEWAY: {
            'description': (
                'Магазин не ответил за 10 с или ответил мусором: цена, источник и '
                'наблюдение НЕ изменились. Покажите «не удалось получить цену с WB», '
                'кнопку оставьте.'
            ),
            'content': {
                'application/json': {
                    'example': {'detail': 'Не удалось получить цену с WB'}
                }
            },
        },
    },
)
def refresh_store_price(
    db: Session = Depends(get_db),
    wish: Wish = Depends(get_current_user_wish),
    store_client: httpx.Client = Depends(get_store_client),
):
    """Кнопка «актуальная с WB»: вернуть источник цены в магазин и подтянуть цену.

    Свежий запрос к магазину по `link` (не последнее наблюдение обхода). Источник
    становится `shop` независимо от наличия товара; `price` меняется только когда
    товар в наличии — магазин цену никогда не обнуляет. Идемпотентно и безвредно
    при уже магазинном источнике (просто обновляет цену и наблюдение); кнопку при
    `price_source = shop` не показывайте, но повтор не ошибка.

    Только автор (`403` у остальных). Тела запроса нет. Архив — не ограничение:
    у архивной хотелки работает так же (`200`), кнопку показывайте по тем же
    правилам (`manual` + `shop != null`); обход архивные не трогает, но явное
    действие юзера — трогает.

    **Длительность:** свежий запрос к магазину — не дольше 10 с, дальше `502`;
    клиентский таймаут ставьте от 15 с, кнопку на это время блокируйте.

    **Аналитика:** каждый вызов (включая `409`/`502`) бэк учитывает как нажатие
    кнопки — счётчик критерия приёмки «нужен ли ручной режим»; клиенту ничего
    дополнительно слать не нужно.
    """
    if wish.shop is None:
        _record_refresh(db, wish, PriceRefreshOutcome.unsupported)
        raise HTTPException(HTTP_409_CONFLICT, 'Ссылка не на поддерживаемый магазин')
    try:
        observation = fetch_fresh_observation(wish.link or '', store_client)
    except (httpx.HTTPError, ValidationError) as error:
        logger.warning(f'«Актуальная с WB» {wish.id}: магазин не ответил: {error!r}')
        _record_refresh(db, wish, PriceRefreshOutcome.failed)
        raise HTTPException(
            HTTP_502_BAD_GATEWAY, 'Не удалось получить цену с WB'
        ) from error
    # `wish.shop` уже проверил, что ссылка — WB, поэтому наблюдение есть.
    assert observation is not None
    wish.price_source = PriceSource.shop
    record_fresh_observation(db, wish, observation, utc_now())
    _record_refresh(db, wish, PriceRefreshOutcome.ok)
    return wish


def _record_refresh(db: Session, wish: Wish, outcome: PriceRefreshOutcome) -> None:
    """Счётчик нажатий «актуальная с WB» — каждое, включая неудачные. Коммит
    здесь же, чтобы событие пережило последующий `HTTPException`."""
    db.add(
        WishPriceRefreshEvent(wish_id=wish.id, user_id=wish.user_id, outcome=outcome)
    )
    db.commit()


@router.delete('/wishes/{wish_id}')
def delete_wish(
    db: Session = Depends(get_db),
    wish: Wish = Depends(get_current_user_wish),
):
    db.delete(wish)
    db.commit()


@router.post('/wishes/{wish_id}/image', responses=IMAGE_UPLOAD_RESPONSES)
def upload_wish_image(
    file: UploadFile,
    wish: Wish = Depends(get_current_user_wish),
    db: Session = Depends(get_db),
):
    """Загрузить фото хотелки (multipart, поле `file`).

    Размер и тип проверяются на сервере (см. коды 413/415). Файл на диске
    получает расширение по реальному типу, чтобы отдаваться с верным Content-Type.
    """
    content, extension = read_uploaded_image(file)
    WISH_IMAGES_DIR.mkdir(exist_ok=True, parents=True)
    content_hash = md5(content).hexdigest()
    file_name = f'{content_hash}{extension}'
    file_path = WISH_IMAGES_DIR / file_name
    file_path.write_bytes(content)
    wish.image = file_name
    db.add(wish)
    db.commit()


@router.delete('/wishes/{wish_id}/image')
def delete_wish_image(
    wish: Wish = Depends(get_current_user_wish),
    db: Session = Depends(get_db),
):
    wish.image = None
    db.add(wish)
    db.commit()


@router.get(
    '/users/{user_id}/wishes',
    response_model=list[WishReadSchema],
    responses={
        200: {
            'description': (
                'Активные хотелки юзера целиком, без пагинации. Пустой список — '
                'желаний нет (не ошибка).'
            )
        },
        HTTP_404_NOT_FOUND: {
            'description': 'Юзера с таким `user_id` нет.',
            'content': {
                'application/json': {'example': {'detail': 'Пользователь не найден'}}
            },
        },
    },
)
def user_wishes(user_id: UUID, db: Session = Depends(get_db)):
    """Хотелки другого юзера — список «чтобы подарить».

    Форма та же, что у автора (`WishReadSchema`), но UI показывает только `price`
    (последняя известная, в т.ч. у распроданного) с «от» при `price_is_minimum`;
    плашки магазина и статусы наличия не показываются — исключение: при
    `store_observation.availability = gone` нейтральное «ссылка устарела» рядом с
    кнопкой перехода по ссылке. Кнопки «актуальная с WB» у чужих нет.
    """
    user = db.scalars(select(User).where(User.id == user_id)).one_or_none()
    if not user:
        raise HTTPException(404, 'Пользователь не найден')
    query = Wish.get_active_wish_query().where(Wish.user == user)
    return db.scalars(query)


@router.post('/wishes/{wish_id}/reserve', response_class=Response)
def reserve_wish(
    wish_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = Wish.get_active_wish_query().where(Wish.id == wish_id)
    wish = db.scalars(query).one_or_none()
    if not wish:
        raise HTTPException(HTTP_404_NOT_FOUND, 'Wish not found')
    if wish.user == current_user:
        raise HTTPException(HTTP_403_FORBIDDEN, 'Cannot reserve own wish')
    if wish.reserved_by and wish.reserved_by != current_user:
        raise HTTPException(HTTP_403_FORBIDDEN, 'Reserved by someone else')
    wish.reserved_by = current_user
    # Момент резерва нужен, чтобы отнести подарок ко времени: без него нельзя
    # проверить, даёт ли повод (радар, пуш) прирост резерваций. У 345 резерваций,
    # сделанных до этой правки, останется NULL — легаси.
    wish.reserved_at = utc_now()
    db.add(wish)
    db.commit()


@router.post('/wishes/{wish_id}/cancel_reservation', response_class=Response)
def cancel_wish_reservation(
    wish_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    wish = db.execute(select(Wish).where(Wish.id == wish_id)).scalar_one_or_none()
    if not wish:
        raise HTTPException(404, 'Wish not found')
    if wish.reserved_by and wish.reserved_by != current_user:
        raise HTTPException(HTTP_403_FORBIDDEN, 'Reserved by someone else')
    wish.reserved_by = None
    wish.reserved_at = None
    db.add(wish)
    db.commit()


@router.post('/wishes/{wish_id}/archive', response_class=Response)
def archive_wish(
    db: Session = Depends(get_db), wish: Wish = Depends(get_current_user_wish)
):
    wish.is_archived = True
    db.add(wish)
    db.commit()


@router.post('/wishes/{wish_id}/unarchive', response_class=Response)
def unarchive_wish(
    db: Session = Depends(get_db), wish: Wish = Depends(get_current_user_wish)
):
    wish.is_archived = False
    db.add(wish)
    db.commit()


@router.get('/archived_wishes', response_model=list[WishReadSchema])
def archived_wishes(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return db.scalars(select(Wish).where(Wish.user == user, Wish.is_archived))
