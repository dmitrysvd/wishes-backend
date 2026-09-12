from datetime import date, datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    HttpUrl,
    field_validator,
)

from app.constants import (
    BirthdayRadarKind,
    FollowSource,
    Gender,
    PriceSource,
    Shop,
    StoreAvailability,
    TestPersona,
)

ItemT = TypeVar('ItemT', bound=BaseModel)


class PageSchema(BaseModel, Generic[ItemT]):
    """Универсальная схема-страница для offset/limit-пагинации."""

    items: list[ItemT]
    total: int
    has_next: bool
    has_previous: bool


WB_LINK_EXAMPLE = (
    'https://www.wildberries.ru/catalog/166652374/detail.aspx?size=306473431'
)
OZON_LINK_EXAMPLE = 'https://www.ozon.ru/product/123456'


class BaseWishSchema(BaseModel):
    name: str = Field(description='Название хотелки.', examples=['Кроссовки'])
    description: str | None = Field(
        description='Описание. null — не заполнено.', examples=['Размер 42, чёрные']
    )
    link: HttpUrl | None = Field(
        description=(
            'Ссылка на товар. null — ссылки нет. Поддерживаемый магазин (живая цена, '
            'фича 0011) — только Wildberries: `wildberries.ru/catalog/<артикул>/…`, '
            'опционально `?size=<optionId>`. Любой другой домен — «неподдерживаемый '
            'магазин»: ссылка хранится и показывается, но цена остаётся ручным полем. '
            'Распознаёт магазин сервер (см. `shop` в ответе), клиент ссылки не '
            'разбирает.'
        ),
        examples=[WB_LINK_EXAMPLE, OZON_LINK_EXAMPLE],
    )


class WishWriteSchema(BaseWishSchema):
    """Форма хотелки целиком — тело `POST /wishes` и `PUT /wishes/{wish_id}`.

    Клиент всегда шлёт все поля формы (в т.ч. `price`), поэтому бэк НЕ может
    отличить «юзер поменял цену» от «фронт переслал загруженное» по значению —
    различение делается только явным флагом `price_edited` (см. его описание).
    Что произошло с ценой в итоге, видно в ответе: `price`, `price_source`,
    `store_observation` (`WishReadSchema`) — отдельный GET после сохранения не нужен.
    """

    model_config = ConfigDict(
        json_schema_extra={
            'examples': [
                {
                    'name': 'Кроссовки',
                    'description': 'Размер 42, чёрные',
                    'price': 4990,
                    'link': WB_LINK_EXAMPLE,
                    'price_edited': False,
                },
                {
                    'name': 'Кроссовки',
                    'description': 'Размер 42, чёрные',
                    'price': 4500,
                    'link': WB_LINK_EXAMPLE,
                    'price_edited': True,
                },
                {
                    'name': 'Кроссовки',
                    'description': None,
                    'price': None,
                    'link': WB_LINK_EXAMPLE,
                    'price_edited': True,
                },
                {
                    'name': 'Кофемолка',
                    'description': None,
                    'price': 3500,
                    'link': OZON_LINK_EXAMPLE,
                    'recommendation_id': '9b2d5e4a-1c3f-4a2b-8d6e-0f1a2b3c4d5e',
                },
            ]
        }
    )

    price: int | None = Field(
        description=(
            'Цена в рублях, целое, как в поле формы. Как применяется — решает '
            '`price_edited` и наличие ссылки на поддерживаемый магазин:\n'
            '- `price_edited = true` — это РУЧНАЯ цена: сохраняется как есть '
            '(null = юзер стёр цену), источник становится `manual`;\n'
            '- `price_edited = false` и ссылка на поддерживаемый магазин — цена у '
            'хотелки магазинная (`shop`), бэк берёт её у магазина сам. Значение из '
            'тела при этом: в `PUT` ИГНОРИРУЕТСЯ (эхо загруженного, могло устареть); '
            'в `POST` — фолбэк: если магазин при сохранении не ответил, сохраняется '
            'это число (цифра превью секунды назад) с `price_source = shop`, '
            '`store_observation = null`, `price_is_minimum = false` (даже если превью '
            'показывало «от» — обход поправит); null в теле → '
            'цены нет до первого наблюдения;\n'
            '- `price_edited = false` и ссылки на поддерживаемый магазин нет — обычное '
            'ручное поле: сохраняется как есть (совместимость со старым клиентом, '
            'который флаг не шлёт).'
        ),
        examples=[4990, None],
    )
    price_edited: bool = Field(
        default=False,
        description=(
            'Юзер ОСОЗНАННО правил поле цены в этой форме (ввёл, изменил, стёр). '
            'Единственный сигнал «ручная правка»: сравнение присланного `price` с '
            'текущим бэк не делает (обход мог обновить цену между загрузкой формы и '
            'сохранением, и совпадение/несовпадение чисел ничего не значит). '
            'true → `price` из тела становится ручной ценой, `price_source = manual`, '
            'магазин с этого момента цену не обновляет и наблюдение не показывается '
            '(вернуть магазинную — `POST /wishes/{wish_id}/refresh_store_price`). '
            'false/опущено → поле цены не трогали: у хотелки со ссылкой на '
            'поддерживаемый магазин источник остаётся/становится `shop`, а `price` '
            'из тела не считается правкой (в `PUT` игнорируется, в `POST` — фолбэк '
            'при недоступном магазине, см. `price`); без такой ссылки `price` '
            'применяется как ручной. '
            'Флаг относится только к этому запросу и не хранится. Сбрасывайте его в '
            'false при открытии формы; ставьте true при любом вводе в поле цены, даже '
            'если юзер вернул прежнюю цифру — это тоже осознанная правка. Это же '
            'правило и когда превью упало (`400` на `POST /item_info_from_page`) или '
            'пришло без цены: WB-ссылка + поле цены не трогали → `shop`, бэк сам '
            'сходит в магазин, а не дождался — цену принесёт обход; юзер ввёл цену '
            'сам → `manual`, обход её не тронет.'
        ),
        examples=[False, True],
    )
    recommendation_id: UUID | None = Field(
        default=None,
        description=(
            'Только для `POST /wishes`: хотелка добавляется из рекомендации — бэк '
            'сам копирует её картинку. В `PUT` игнорируется. null/опущено — обычное '
            'добавление.'
        ),
    )


class RecommendationSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    description: str | None
    price: int | None
    link: str
    image_url: str | None


class RecommendationCreateSchema(BaseModel):
    title: str
    description: str | None = None
    price: int | None = None
    link: HttpUrl
    image_url: HttpUrl | None = None


class RecommendationFullReadSchema(RecommendationSchema):
    model_config = ConfigDict(from_attributes=True)

    wishes_count: int = 0


class BaseUserSchema(BaseModel):
    id: UUID
    display_name: str
    photo_url: HttpUrl | None
    gender: Gender | None
    birth_date: date | None


class OtherUserSchema(BaseUserSchema):
    model_config = ConfigDict(from_attributes=True)

    email: EmailStr | None = Field(
        default=None,
        deprecated=True,
        description=(
            'НЕ ИСПОЛЬЗУЕТСЯ. Для чужого пользователя всегда `null` — email это '
            'PII и наружу не отдаётся. Поле оставлено в контракте ради обратной '
            'совместимости; свой email смотри в `CurrentUserReadSchema.email`.'
        ),
    )

    @field_validator('email', mode='before')
    @staticmethod
    def hide_email(_: object) -> None:
        # Чужой email наружу не отдаём (PII): зануляем независимо от значения в БД.
        return None


class StoreObservationSchema(BaseModel):
    """Последнее наблюдение товара в магазине — наличие и когда снято.

    Есть только у хотелки с `price_source = shop` и только после того, как магазин
    хоть раз ответил (превью/сохранение/кнопка — свежий запрос, либо суточный обход).
    """

    availability: StoreAvailability = Field(
        description=(
            'Наличие по последнему наблюдению. `in_stock` — товар есть, `price` '
            'актуальна. `sold_out` — карточка есть, товар распродан: `price` — '
            'последняя цена, когда он был в наличии (или null, если такого не было). '
            '`gone` — артикул исчез из магазина, ссылка мёртвая; `price` — как при '
            '`sold_out`. `gone` появляется ТОЛЬКО из суточного обхода: одиночный '
            'свежий запрос (превью, сохранение, кнопка) исчезнувшим товар не '
            'объявляет — пустой ответ магазина для него неотличим от сбоя. Пометки '
            '(«распродано на WB» / «товара больше нет на WB») показывайте только '
            'автору; чужим — никаких статусов наличия, без исключений.'
        ),
        examples=['in_stock'],
    )
    observed_at: datetime = Field(
        description=(
            'Когда снято наблюдение, UTC, ISO 8601. Давность для плашки автора '
            '(«сегодня»/«вчера»/«3 дня назад») считайте на клиенте. Порога «слишком '
            'старое» нет: любую давность показываем как есть. У хотелки в архиве '
            'обход останавливается, давность просто растёт.'
        ),
        examples=['2026-09-12T03:10:00Z'],
    )


class WishReadSchema(BaseWishSchema):
    """Хотелка во всех списках и карточках приложения.

    Одна и та же форма у автора и у чужих (`GET /users/{user_id}/wishes`, резервы),
    различие — в том, что UI показывает. Автор: `price` с плашкой магазина и давности
    (`shop` + `store_observation.observed_at`), пометки наличия
    (`store_observation.availability`), кнопка «актуальная с WB» при
    `price_source = manual` и `shop != null`. Чужие: только `price` (последняя
    известная, в т.ч. у распроданного) с «от» при `price_is_minimum`, без плашек и
    статусов наличия — без исключений (поля отдаются, но чужой UI их не
    показывает). Источник цены и кнопку меняет только автор (`403` у остальных).

    Все поля присутствуют всегда; «нет значения» — это `null`, а не отсутствие
    ключа. Примеры (по порядку): в наличии; «от» + распродано; сразу после
    добавления при недоступном WB (цена и наблюдение ещё пусты); ручная цена при
    WB-ссылке (кнопка «актуальная с WB»); без поддерживаемого магазина; товар исчез
    (`gone`, цена — последняя известная); распродано, цены не было никогда;
    существующая хотелка после релиза (цена введена руками при добавлении,
    источник уже `shop`, обход ещё не прошёл).
    """

    model_config = ConfigDict(
        json_schema_extra={
            'examples': [
                {
                    'id': '7c9e6679-7425-40de-944b-e07fc1f90ae7',
                    'name': 'Кроссовки',
                    'description': 'Размер 42, чёрные',
                    'link': WB_LINK_EXAMPLE,
                    'price': 4990,
                    'price_source': 'shop',
                    'price_is_minimum': False,
                    'shop': 'wildberries',
                    'store_observation': {
                        'availability': 'in_stock',
                        'observed_at': '2026-09-12T03:10:00Z',
                    },
                    'is_archived': False,
                    'reserved_by_id': None,
                    'image': '/media/wish_images/ab12cd34.jpg',
                    'recommendation_id': None,
                    'user': {
                        'id': '3fa85f64-5717-4562-b3fc-2c963f66afa6',
                        'display_name': 'Аня',
                        'photo_url': None,
                        'gender': None,
                        'birth_date': None,
                        'email': None,
                    },
                },
                {
                    'id': '7c9e6679-7425-40de-944b-e07fc1f90ae7',
                    'name': 'Платье',
                    'description': None,
                    'link': 'https://www.wildberries.ru/catalog/166652374/detail.aspx',
                    'price': 1990,
                    'price_source': 'shop',
                    'price_is_minimum': True,
                    'shop': 'wildberries',
                    'store_observation': {
                        'availability': 'sold_out',
                        'observed_at': '2026-09-09T03:10:00Z',
                    },
                    'is_archived': False,
                    'reserved_by_id': None,
                    'image': None,
                    'recommendation_id': None,
                    'user': {
                        'id': '3fa85f64-5717-4562-b3fc-2c963f66afa6',
                        'display_name': 'Аня',
                        'photo_url': None,
                        'gender': None,
                        'birth_date': None,
                        'email': None,
                    },
                },
                {
                    'id': '7c9e6679-7425-40de-944b-e07fc1f90ae7',
                    'name': 'Кроссовки',
                    'description': None,
                    'link': WB_LINK_EXAMPLE,
                    'price': None,
                    'price_source': 'shop',
                    'price_is_minimum': False,
                    'shop': 'wildberries',
                    'store_observation': None,
                    'is_archived': False,
                    'reserved_by_id': None,
                    'image': None,
                    'recommendation_id': None,
                    'user': {
                        'id': '3fa85f64-5717-4562-b3fc-2c963f66afa6',
                        'display_name': 'Аня',
                        'photo_url': None,
                        'gender': None,
                        'birth_date': None,
                        'email': None,
                    },
                },
                {
                    'id': '7c9e6679-7425-40de-944b-e07fc1f90ae7',
                    'name': 'Кроссовки',
                    'description': None,
                    'link': WB_LINK_EXAMPLE,
                    'price': 4500,
                    'price_source': 'manual',
                    'price_is_minimum': False,
                    'shop': 'wildberries',
                    'store_observation': None,
                    'is_archived': False,
                    'reserved_by_id': None,
                    'image': None,
                    'recommendation_id': None,
                    'user': {
                        'id': '3fa85f64-5717-4562-b3fc-2c963f66afa6',
                        'display_name': 'Аня',
                        'photo_url': None,
                        'gender': None,
                        'birth_date': None,
                        'email': None,
                    },
                },
                {
                    'id': '9b2d5e4a-1c3f-4a2b-8d6e-0f1a2b3c4d5e',
                    'name': 'Кофемолка',
                    'description': None,
                    'link': OZON_LINK_EXAMPLE,
                    'price': 3500,
                    'price_source': 'manual',
                    'price_is_minimum': False,
                    'shop': None,
                    'store_observation': None,
                    'is_archived': False,
                    'reserved_by_id': '3fa85f64-5717-4562-b3fc-2c963f66afa6',
                    'image': None,
                    'recommendation_id': '9b2d5e4a-1c3f-4a2b-8d6e-0f1a2b3c4d5e',
                    'user': {
                        'id': '3fa85f64-5717-4562-b3fc-2c963f66afa6',
                        'display_name': 'Аня',
                        'photo_url': None,
                        'gender': None,
                        'birth_date': None,
                        'email': None,
                    },
                },
                {
                    'id': '7c9e6679-7425-40de-944b-e07fc1f90ae7',
                    'name': 'Наушники',
                    'description': None,
                    'link': WB_LINK_EXAMPLE,
                    'price': 2490,
                    'price_source': 'shop',
                    'price_is_minimum': False,
                    'shop': 'wildberries',
                    'store_observation': {
                        'availability': 'gone',
                        'observed_at': '2026-09-12T03:10:00Z',
                    },
                    'is_archived': False,
                    'reserved_by_id': None,
                    'image': None,
                    'recommendation_id': None,
                    'user': {
                        'id': '3fa85f64-5717-4562-b3fc-2c963f66afa6',
                        'display_name': 'Аня',
                        'photo_url': None,
                        'gender': None,
                        'birth_date': None,
                        'email': None,
                    },
                },
                {
                    'id': '7c9e6679-7425-40de-944b-e07fc1f90ae7',
                    'name': 'Куртка',
                    'description': None,
                    'link': WB_LINK_EXAMPLE,
                    'price': None,
                    'price_source': 'shop',
                    'price_is_minimum': False,
                    'shop': 'wildberries',
                    'store_observation': {
                        'availability': 'sold_out',
                        'observed_at': '2026-09-12T03:10:00Z',
                    },
                    'is_archived': False,
                    'reserved_by_id': None,
                    'image': None,
                    'recommendation_id': None,
                    'user': {
                        'id': '3fa85f64-5717-4562-b3fc-2c963f66afa6',
                        'display_name': 'Аня',
                        'photo_url': None,
                        'gender': None,
                        'birth_date': None,
                        'email': None,
                    },
                },
                {
                    'id': '7c9e6679-7425-40de-944b-e07fc1f90ae7',
                    'name': 'Рюкзак',
                    'description': None,
                    'link': WB_LINK_EXAMPLE,
                    'price': 3200,
                    'price_source': 'shop',
                    'price_is_minimum': False,
                    'shop': 'wildberries',
                    'store_observation': None,
                    'is_archived': False,
                    'reserved_by_id': None,
                    'image': None,
                    'recommendation_id': None,
                    'user': {
                        'id': '3fa85f64-5717-4562-b3fc-2c963f66afa6',
                        'display_name': 'Аня',
                        'photo_url': None,
                        'gender': None,
                        'birth_date': None,
                        'email': None,
                    },
                },
            ]
        }
    )

    id: UUID = Field(description='Идентификатор хотелки.')
    price: int | None = Field(
        description=(
            'Цена в рублях (целое, копейки отбрасываются; со скидкой — та, по '
            'которой покупают). Чья она — '
            'в `price_source`. При `shop` — последняя известная цена с магазина: '
            'магазин её НИКОГДА не обнуляет, при распродано/исчез остаётся последняя '
            'наблюдённая; null — цены не было никогда (обход/превью ещё не видели '
            'товар в наличии). При `manual` — введённая юзером; null — не указана. '
            'Для чужих это ориентир для подарка, показывайте как есть.'
        ),
        examples=[4990, None],
    )
    price_source: PriceSource = Field(
        description=(
            'Откуда цена. `shop` — живая, с магазина (превью при добавлении + '
            'суточный обход); юзер её не вводил. `manual` — введена/исправлена '
            'руками (`price_edited = true`) либо ссылки на поддерживаемый магазин '
            'нет: магазин молчит — ни плашки, ни обновлений. `shop` бывает только '
            'при `shop != null`. После релиза 0011 все существующие хотелки с '
            'WB-ссылкой — `shop` (в т.ч. с ценой, введённой руками: та цифра — снимок '
            'на момент добавления, обход её обновит), остальные — `manual`.'
        ),
        examples=['shop', 'manual'],
    )
    price_is_minimum: bool = Field(
        description=(
            'true — `price` это минимум среди размеров в наличии (ссылка без '
            '`?size=` у многоразмерного товара): рисуйте «от 1 990 ₽», чтобы цифра '
            'не врала про другой размер. Во всех UI, включая чужие карточки. '
            'false — цена конкретного размера/безразмерного товара или ручная. '
            'Флаг привязан к `price` и меняется только вместе с ним: пришла новая '
            'магазинная цена → пересчитан; цена стала ручной (`price_edited = true`, '
            'ссылка на неподдерживаемый магазин/удалена) → false; цена не менялась '
            '(распродано/исчез, магазин недоступен) → как был.'
        ),
        examples=[False, True],
    )
    shop: Shop | None = Field(
        description=(
            'Магазин, распознанный сервером по `link`. null — ссылки нет или магазин '
            'не поддерживается (цена — ручное поле, магазинных элементов в UI нет). '
            'Не null → плашка магазина у автора при `price_source = shop`; кнопка '
            '«актуальная с WB» у автора при `price_source = manual`.'
        ),
        examples=['wildberries', None],
    )
    store_observation: StoreObservationSchema | None = Field(
        description=(
            'Последнее наблюдение магазина: наличие и давность. null — либо '
            '`price_source = manual` (магазин молчит), либо магазин ещё ни разу не '
            'ответил по этой ссылке (магазинная хотелка сразу после релиза до первого '
            'обхода, или WB был недоступен при добавлении): автору показывайте '
            'плашку «WB» без давности и без пометок наличия.'
        ),
    )
    is_archived: bool = Field(description='Хотелка в архиве автора.')
    reserved_by_id: UUID | None = Field(
        description='Кто зарезервировал. null — свободна.'
    )
    image: str | None = Field(
        description=(
            'Абсолютный путь картинки от origin API (`/media/wish_images/…`). '
            'null — картинки нет.'
        ),
        examples=['/media/wish_images/ab12cd34.jpg', None],
    )
    recommendation_id: UUID | None = Field(
        description='Рекомендация, из которой добавлена хотелка. null — добавлена сама.'
    )
    user: OtherUserSchema = Field(description='Автор хотелки.')

    @field_validator('image', mode='before')
    @staticmethod
    def make_image_url(image_name: str) -> str | None:
        if not image_name:
            return None
        return f'/media/wish_images/{image_name}'


class PublicBirthdaySchema(BaseModel):
    """День рождения владельца без года — день и месяц.

    Год намеренно не отдаётся: публичная страница открыта без авторизации, а
    год рождения/возраст — PII. Поле для виджета «скоро день рождения».
    """

    day: int = Field(description='День месяца, 1–31.', ge=1, le=31, examples=[15])
    month: int = Field(description='Месяц, 1–12.', ge=1, le=12, examples=[3])


class PublicOwnerSchema(BaseModel):
    """Публичные данные владельца вишлиста.

    Никакого PII: email, телефон и год рождения наружу не отдаются.
    """

    id: UUID = Field(
        description='Идентификатор владельца; совпадает с user_id в пути запроса.'
    )
    display_name: str = Field(
        description='Отображаемое имя владельца.', examples=['Аня']
    )
    photo_url: HttpUrl | None = Field(
        default=None,
        description='URL аватара. null — фото не задано, показывайте плейсхолдер.',
        examples=['https://lh3.googleusercontent.com/a/default-user'],
    )
    birthday: PublicBirthdaySchema | None = Field(
        default=None,
        description=(
            'День и месяц дня рождения (без года). null — владелец не указал '
            'дату рождения.'
        ),
    )


class PublicWishSchema(BaseModel):
    """Одна активная хотелка владельца на публичной странице.

    Архивные хотелки в список не попадают. Личность зарезервировавшего не
    раскрывается — только булев `is_reserved` (анти-спойлер для владельца).
    """

    id: UUID = Field(description='Идентификатор хотелки; стабильный ключ для списка.')
    name: str = Field(description='Название хотелки.', examples=['Кофемолка'])
    description: str | None = Field(
        default=None,
        description='Описание хотелки. null — владелец не заполнил.',
        examples=['Ручная, с керамическими жерновами'],
    )
    price: int | None = Field(
        default=None,
        description=(
            'Ориентировочная цена, целое число рублей. Для хотелки со ссылкой на '
            'поддерживаемый магазин — последняя известная цена с магазина (и у '
            'распроданного тоже), иначе введённая владельцем. null — цена не '
            'указана / магазин её ещё не сообщал. Без плашек магазина и статусов '
            'наличия — гость видит цену как ориентир.'
        ),
        examples=[3500],
    )
    price_is_minimum: bool = Field(
        description=(
            'Всегда присутствует. true — `price` это минимум среди размеров в '
            'наличии (ссылка без размера у многоразмерного товара): рисуйте '
            '«от 1 990 ₽». false — обычная цена.'
        ),
        examples=[False],
    )
    link: HttpUrl | None = Field(
        default=None,
        description='Ссылка на товар в магазине. null — ссылки нет.',
        examples=['https://www.ozon.ru/product/123456'],
    )
    image_url: str | None = Field(
        default=None,
        description=(
            'Абсолютный путь картинки от origin этого API: значение уже включает '
            'префикс `/media/wish_images/`. Полный URL = origin API + это значение '
            '(напр. origin `https://hotelki.pro` + `/media/wish_images/ab12cd34.jpg`). '
            'null — картинки нет, показывайте плейсхолдер.'
        ),
        examples=['/media/wish_images/ab12cd34.jpg'],
    )
    is_reserved: bool = Field(
        description=(
            'Зарезервирована ли хотелка кем-либо. true — подарок уже выбран '
            'другим дарителем; false — свободна. Кто именно зарезервировал, '
            'публично не раскрывается (анти-спойлер).'
        ),
        examples=[False],
    )


class PublicWishlistSchema(BaseModel):
    """Публичный вишлист: владелец + его активные хотелки (read-only)."""

    model_config = ConfigDict(
        json_schema_extra={
            'examples': [
                {
                    'owner': {
                        'id': '3fa85f64-5717-4562-b3fc-2c963f66afa6',
                        'display_name': 'Аня',
                        'photo_url': 'https://lh3.googleusercontent.com/a/default-user',
                        'birthday': {'day': 15, 'month': 3},
                    },
                    'wishes': [
                        {
                            'id': '7c9e6679-7425-40de-944b-e07fc1f90ae7',
                            'name': 'Кофемолка',
                            'description': 'Ручная, с керамическими жерновами',
                            'price': 3500,
                            'price_is_minimum': False,
                            'link': 'https://www.ozon.ru/product/123456',
                            'image_url': '/media/wish_images/ab12cd34.jpg',
                            'is_reserved': False,
                        },
                        {
                            'id': '9b2d5e4a-1c3f-4a2b-8d6e-0f1a2b3c4d5e',
                            'name': 'Книга «Дюна»',
                            'description': None,
                            'price': None,
                            'price_is_minimum': False,
                            'link': None,
                            'image_url': None,
                            'is_reserved': True,
                        },
                    ],
                },
                {
                    'owner': {
                        'id': '3fa85f64-5717-4562-b3fc-2c963f66afa6',
                        'display_name': 'Аня',
                        'photo_url': None,
                        'birthday': None,
                    },
                    'wishes': [],
                },
            ]
        }
    )

    owner: PublicOwnerSchema = Field(description='Владелец вишлиста.')
    wishes: list[PublicWishSchema] = Field(
        description=(
            'Активные хотелки владельца, отдаются ЦЕЛИКОМ — без пагинации, лимита '
            'и курсора (список одного человека невелик). Порядок не гарантирован. '
            'Пустой список — у владельца пока нет желаний (это НЕ ошибка): '
            'показывайте заглушку и CTA, не пустой экран.'
        ),
    )


class BirthdayRadarEntrySchema(BaseModel):
    """Одна строка бёрздей-радара — приближающийся день рождения человека.

    Два вида строки различаются полем `kind` (см. `BirthdayRadarKind`):
    - `in_app` — у человека есть аккаунт: заполнены `user_id`, `active_wishes_count`,
      `followed_by_me`; `vk_id` = null. Тап ведёт в его список (S5).
    - `invite` — человек только среди VK-друзей, аккаунта нет: заполнен `vk_id`;
      `user_id`/`active_wishes_count`/`followed_by_me` = null. Показываем «Пригласить»
      (шеринг инвайт-ссылки текущего юзера из `GET /invite_link/`).

    Поля `display_name`, `birthday`, `days_until_birthday` присутствуют всегда (оба
    вида). `photo_url` тоже общий для обоих видов и может быть null (см. его описание) —
    рендерите плейсхолдер. `birthday` всегда известен: строки без известной даты в радар
    не попадают. Год не отдаётся намеренно: это PII третьего лица (как на S5a).
    """

    kind: BirthdayRadarKind = Field(
        description=(
            'Вид строки: `in_app` — есть аккаунт (веди в список), `invite` — только '
            'VK-друг без аккаунта (предложи пригласить). Определяет, какие поля '
            'заполнены (см. описание схемы).'
        ),
        examples=['in_app'],
    )
    display_name: str = Field(
        description=(
            'Отображаемое имя. Для `in_app` — имя из профиля; для `invite` — имя '
            'VK-друга (имя + фамилия из VK).'
        ),
        examples=['Аня'],
    )
    photo_url: HttpUrl | None = Field(
        default=None,
        description=(
            'URL аватара. Для `in_app` — фото из профиля аккаунта (или null). Для '
            '`invite` — аватар VK-друга; может быть null, если снимок VK-друзей снят '
            'до того, как бэк начал собирать фото (заполнится при следующем входе '
            'владельца или ручном бэкфиле). null → показывайте плейсхолдер.'
        ),
        examples=['https://lh3.googleusercontent.com/a/default-user'],
    )
    birthday: PublicBirthdaySchema = Field(
        description=(
            'День и месяц дня рождения (без года). Всегда присутствует — строки без '
            'известной даты в радар не включаются.'
        ),
    )
    days_until_birthday: int = Field(
        description=(
            'Сколько дней до ближайшего дня рождения (0 — сегодня, 1 — завтра). '
            'Считается сервером от текущей даты. По этому полю список уже '
            'отсортирован по возрастанию — ближайшие ДР сверху.'
        ),
        ge=0,
        examples=[5],
    )
    user_id: UUID | None = Field(
        default=None,
        description=(
            'Идентификатор аккаунта в приложении — для навигации в его список (S5). '
            'Заполнен только при `kind = in_app`; для `invite` = null.'
        ),
        examples=['3fa85f64-5717-4562-b3fc-2c963f66afa6'],
    )
    active_wishes_count: int | None = Field(
        default=None,
        description=(
            'Число активных (не архивных) хотелок. `0` — список пуст (покажите '
            '«список пуст» без давления, без CTA), `>0` — есть что подарить (CTA '
            '«Посмотреть список»). Заполнено только при `kind = in_app`; для '
            '`invite` = null.'
        ),
        ge=0,
        examples=[3],
    )
    followed_by_me: bool | None = Field(
        default=None,
        description=(
            'Подписан ли текущий юзер на этого человека (подсказка для кнопки '
            'подписки). Заполнено только при `kind = in_app`; для `invite` = null.'
        ),
        examples=[False],
    )
    vk_id: str | None = Field(
        default=None,
        description=(
            'VK id VK-друга — стабильный ключ строки и, при желании, ссылка на его '
            'VK-профиль. Заполнен только при `kind = invite`; для `in_app` = null.'
        ),
        examples=['123456789'],
    )


class BirthdayRadarSchema(BaseModel):
    """Бёрздей-радар: приближающиеся ДР VK-друзей и подписок текущего юзера.

    Источник — VK-друзья юзера (из данных VK) плюс те, на кого он подписан в
    приложении и кто указал дату рождения; дубли схлопнуты (человек в списке один
    раз, при наличии аккаунта — как `in_app`). Год рождения наружу не отдаётся (PII
    третьего лица).
    """

    model_config = ConfigDict(
        json_schema_extra={
            'examples': [
                {
                    'vk_linked': True,
                    'entries': [
                        {
                            'kind': 'in_app',
                            'display_name': 'Аня',
                            'photo_url': 'https://lh3.googleusercontent.com/a/default-user',
                            'birthday': {'day': 26, 'month': 7},
                            'days_until_birthday': 3,
                            'user_id': '3fa85f64-5717-4562-b3fc-2c963f66afa6',
                            'active_wishes_count': 3,
                            'followed_by_me': True,
                            'vk_id': None,
                        },
                        {
                            'kind': 'invite',
                            'display_name': 'Пётр Смирнов',
                            'photo_url': 'https://sun9-1.userapi.com/s/v1/ig2/pyotr.jpg',
                            'birthday': {'day': 2, 'month': 8},
                            'days_until_birthday': 10,
                            'user_id': None,
                            'active_wishes_count': None,
                            'followed_by_me': None,
                            'vk_id': '123456789',
                        },
                        {
                            'kind': 'in_app',
                            'display_name': 'Игорь',
                            'photo_url': None,
                            'birthday': {'day': 20, 'month': 8},
                            'days_until_birthday': 28,
                            'user_id': '9b2d5e4a-1c3f-4a2b-8d6e-0f1a2b3c4d5e',
                            'active_wishes_count': 0,
                            'followed_by_me': False,
                            'vk_id': None,
                        },
                    ],
                },
                {'vk_linked': False, 'entries': []},
                {'vk_linked': True, 'entries': []},
            ]
        }
    )

    vk_linked: bool = Field(
        description=(
            'Привязан ли у текущего юзера VK. Нужно, чтобы различить два пустых '
            'состояния радара: `false` + пустой `entries` → показать CTA «Привяжи '
            'VK, чтобы видеть ДР друзей»; `true` + пустой `entries` → «Пока не нашли '
            'дни рождения среди друзей».'
        ),
        examples=[True],
    )
    entries: list[BirthdayRadarEntrySchema] = Field(
        description=(
            'Строки радара, уже отсортированные по возрастанию `days_until_birthday` '
            '(ближайшие ДР сверху); при равном числе дней — по `display_name` '
            '(лексикографически, стабильный детерминированный порядок). Отдаются '
            'ЦЕЛИКОМ, без пагинации. Пустой список — нет известных ближайших ДР (это '
            'не ошибка); какое пустое состояние показать, различайте по `vk_linked`.'
        ),
    )


class AnnotatedOtherUserSchema(BaseUserSchema):
    model_config = ConfigDict(from_attributes=True)

    follows: list[OtherUserSchema]
    followed_by: list[OtherUserSchema]
    follows_me: bool
    followed_by_me: bool


class CurrentUserReadSchema(BaseUserSchema):
    phone: str | None
    email: EmailStr | None
    follows: list[OtherUserSchema]
    followed_by: list[OtherUserSchema]


class CurrentUserUpdateSchema(BaseModel):
    display_name: str
    gender: Gender
    birth_date: date | None


class RegistrationAttributionSchema(BaseModel):
    """Метка атрибуции, донесённая клиентом от инвайт-ссылки/точки входа до момента
    регистрации.

    Передаётся опционально в любом из auth-вызовов. **Best-effort:** невалидные
    значения молча игнорируются и НИКОГДА не валят регистрацию. Применяется только
    при создании **нового** юзера (first-touch); при повторном логине игнорируется
    целиком — ранее сохранённая атрибуция не перезаписывается.
    """

    referrer_id: str | None = Field(
        default=None,
        description=(
            'Кто пригласил — id юзера-владельца инвайт-ссылки (параметр `ref` из '
            'deep link), передаётся как строка «как есть» из URL. Тип НЕ `uuid` '
            'намеренно: клиент шлёт значение без пред-валидации, а бэк сам валидирует '
            'его как UUID и **тихо отбрасывает** синтаксически-битое, несуществующее '
            'или self-значение — без `422`, регистрация всегда проходит (best-effort). '
            'Сохраняется, только если строка — валидный UUID существующего юзера, не '
            'равного регистрирующемуся. `null`/опущено = органик '
            '(не по чьей-то ссылке).'
        ),
        examples=['7c9e6679-7425-40de-944b-e07fc1f90ae7'],
    )
    utm_source: str | None = Field(
        default=None,
        description=(
            'Канал входа, проставленный клиентом (мессенджер шеринга, лендинг, '
            'рекламная кампания и т.п.). Свободная строка без ограничения длины на '
            'проводе: переразмерное значение НЕ даёт `422` — бэк молча усекает его до '
            'внутреннего лимита (64 символа), best-effort. Нормализация/группировка — '
            'на стороне аналитики. `null`/опущено = канал неизвестен.'
        ),
        examples=['telegram', 'vk', 'whatsapp', 'organic'],
    )


class RequestFirebaseAuthSchema(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            'examples': [
                {
                    'id_token': 'eyJhbGciOi...firebase-id-token',
                    'attribution': {
                        'referrer_id': '7c9e6679-7425-40de-944b-e07fc1f90ae7',
                        'utm_source': 'telegram',
                    },
                },
                {'id_token': 'eyJhbGciOi...firebase-id-token'},
            ]
        }
    )

    id_token: str
    attribution: RegistrationAttributionSchema | None = Field(
        default=None,
        description=(
            'Атрибуция установки/реферала, учитывается только при создании нового '
            'юзера. Опущено/`null` = без атрибуции.'
        ),
    )


class SavePushTokenSchema(BaseModel):
    # Пустой токен бессмыслен: пуш по нему не уйдёт, а «нет токена» кодируется
    # как NULL в БД. min_length=1 не пускает '' в колонку (см. CHECK-констрейнт
    # push_token_not_empty на модели User).
    push_token: str = Field(min_length=1)


class FollowActionSchema(BaseModel):
    """Опциональное тело `POST /follow` и `/unfollow`.

    Несёт только аналитическую метку источника — на саму подписку/отписку не
    влияет. Тело целиком опционально: старые клиенты шлют пустой запрос, событие
    всё равно логируется с `source = null`.
    """

    model_config = ConfigDict(
        json_schema_extra={'examples': [{'source': 'search'}, {}]}
    )

    source: FollowSource | None = Field(
        default=None,
        description=(
            'Экран-источник, с которого пришли на профиль перед действием '
            '(аналитика формирования графа). Опущено/`null` = источник неизвестен '
            '(в т.ч. клиент ещё не шлёт метку). На результат не влияет.'
        ),
        examples=['search', 'possible_friends'],
    )


class RequestVkAuthMobileSchema(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            'examples': [
                {
                    'access_token': 'vk-access-token',
                    'email': 'user@example.com',
                    'phone': '+70000000000',
                    'attribution': {
                        'referrer_id': '7c9e6679-7425-40de-944b-e07fc1f90ae7',
                        'utm_source': 'whatsapp',
                    },
                },
                {
                    'access_token': 'vk-access-token',
                    'email': None,
                    'phone': None,
                },
            ]
        }
    )

    access_token: str
    email: str | None
    phone: str | None
    attribution: RegistrationAttributionSchema | None = Field(
        default=None,
        description=(
            'Атрибуция установки/реферала, учитывается только при создании нового '
            'юзера. Опущено/`null` = без атрибуции.'
        ),
    )


class RequestVkAuthVkidSchema(BaseModel):
    """Вход через VK ID (Confidential Flow, OAuth 2.1) — единый для web и мобилок.

    Платформо-нейтральный контракт: VK ID SDK (веб-виджет One Tap `@vkid/sdk` или
    нативный SDK на устройстве) проводит авторизацию с per-request PKCE и `state`
    внутри себя и отдаёт клиенту **authorization code**, а не готовый токен. Клиент
    пересылает `code` бэку, и обмен `code → access_token` идёт **на сервере**.
    Почему не токен напрямую (как в легаси `/auth/vk/mobile` / silent_token
    `/auth/vk/web`): в Public Flow VK привязывает `access_token` к IP клиента, и
    серверная валидация с IP датацентра невозможна; Confidential Flow привязывает
    токен к IP бэка, который его и использует. `client_secret` сервер не раскрывает.

    Email/phone в теле НЕ передаются намеренно: подтверждённый email бэк берёт из
    `id_token` VK ID (доверенный источник), а не из тела клиента — иначе возможен
    захват чужого аккаунта подстановкой чужого email при связывании по email.

    Сайд-эффект (атрибуция): при первичном создании юзера (`user_created=true`)
    учитывается `attribution` (first-touch, best-effort). Для существующего юзера
    игнорируется. См. `RegistrationAttributionSchema`.
    """

    model_config = ConfigDict(
        json_schema_extra={
            'examples': [
                {
                    'code': 'vk1.a.authorization-code-from-sdk',
                    'code_verifier': 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk',
                    'device_id': 'vk-device-id-from-sdk',
                    'redirect_uri': 'https://hotelki.pro/',
                    'attribution': {
                        'referrer_id': '7c9e6679-7425-40de-944b-e07fc1f90ae7',
                        'utm_source': 'vk',
                    },
                },
                {
                    'code': 'vk1.a.authorization-code-from-sdk',
                    'code_verifier': 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk',
                    'device_id': 'vk-device-id-from-sdk',
                    'redirect_uri': 'https://hotelki.pro/',
                },
            ]
        }
    )

    code: str = Field(
        description=(
            'Одноразовый authorization code из VK ID SDK (веб One Tap `code` или '
            '`ConfidentialFlowData.code` на мобилке). Бэк обменивает его на токены у '
            'VK ID Backend. Повторный обмен уже использованного/истёкшего `code` → '
            '`401`.'
        )
    )
    code_verifier: str = Field(
        description=(
            'PKCE `code_verifier`, сгенерированный SDK под этот `code`. Бэк передаёт '
            'его в обмене; VK сверяет с `code_challenge` из шага авторизации. '
            'Несовпадение → `401`.'
        )
    )
    device_id: str = Field(
        description=(
            'Идентификатор устройства/сессии из VK ID SDK (веб `device_id` или '
            '`ConfidentialFlowData.deviceId`). Требуется VK ID при обмене кода.'
        )
    )
    redirect_uri: str = Field(
        description=(
            '`redirect_uri`, с которым SDK проводил авторизацию: на вебе — https-'
            'origin приложения (напр. `https://hotelki.pro/`); на мобилке — кастомная '
            'схема `vk<app_id>://…`, зашитая в нативный SDK. Бэк передаёт его в обмене '
            'как есть; VK сверяет байт-в-байт с шагом авторизации. Задаёт клиент (а не '
            'сервер), т.к. значение известно SDK. Веб и мобилка — ВСЕГДА разные '
            'VK ID-приложения, поэтому по СХЕМЕ `redirect_uri` бэк выбирает, под '
            'каким VK-app обменивать `code`: `http(s)://…` → веб-app, иная '
            '(`vk…://`) схема → '
            'мобильный app. Несовпадение → `401` (`invalid_request`).'
        )
    )
    attribution: RegistrationAttributionSchema | None = Field(
        default=None,
        description=(
            'Атрибуция установки/реферала, учитывается только при создании нового '
            'юзера. Опущено/`null` = без атрибуции.'
        ),
    )


class ResponseVkAuthMobileSchema(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            'examples': [
                {
                    'firebase_uid': 'firebase-uid-abc123',
                    'firebase_token': 'eyJhbGciOi...firebase-custom-token',
                    'user_created': True,
                }
            ]
        }
    )

    firebase_uid: str = Field(
        description='UID пользователя в Firebase. Стабильный идентификатор аккаунта.'
    )
    firebase_token: str = Field(
        description=(
            'Кастомный Firebase-токен. Клиент передаёт его в '
            '`signInWithCustomToken`, чтобы залогиниться в Firebase; дальнейшие '
            'запросы к API идут с полученным Firebase ID-токеном.'
        )
    )
    user_created: bool = Field(
        description=(
            '`true` — аккаунт создан этим запросом впервые (первый вход); '
            '`false` — вход в существующий аккаунт. Влияет на учёт `attribution` '
            '(учитывается только при `true`).'
        )
    )


class TestTokenRequestSchema(BaseModel):
    """Запрос токена сид-юзера для авто-тестов (фича 0009)."""

    model_config = ConfigDict(
        json_schema_extra={
            'examples': [{'secret': 'shared-dev-secret', 'persona': 'rich'}]
        }
    )

    secret: str = Field(
        description=(
            'Общий dev/test-секрет из окружения бэка (НЕ пароль пользователя). '
            'Неверный/пустой → `403`. Утечка секрета не раскрывает данные прода: '
            'токен выдаётся только сид-юзерам.'
        )
    )
    persona: TestPersona = Field(
        default=TestPersona.rich,
        description=(
            'Какого сид-юзера вернуть. `rich` — привязан VK, друзья-с-ДР, подписки '
            'и желания (данные для радара/списков); `empty` — без VK и без данных '
            '(пустые состояния). Опущено = `rich`.'
        ),
    )


class TestTokenResponseSchema(BaseModel):
    """Ответ с bearer-токеном сид-юзера (фича 0009)."""

    model_config = ConfigDict(
        json_schema_extra={
            'examples': [
                {
                    'token': 'shared-dev-secret:3fa85f64-5717-4562-b3fc-2c963f66afa6',
                    'persona': 'rich',
                    'user_id': '3fa85f64-5717-4562-b3fc-2c963f66afa6',
                }
            ]
        }
    )

    token: str = Field(
        description=(
            'Готовый bearer сид-юзера: кладётся в заголовок `Authorization` как '
            'есть, эквивалентен токену после OAuth — существующий клиент '
            '(`ApiRepository`, `login.sh`) работает без изменений. Без явного TTL: '
            'валиден, пока сконфигурен тот же секрет и сид-юзер существует.'
        )
    )
    persona: TestPersona = Field(
        description='Персона выданного сид-юзера (эхо запроса).'
    )
    user_id: UUID = Field(
        description='UUID сид-юзера — стабилен между вызовами (get-or-create).'
    )


class ItemInfoRequestSchema(BaseModel):
    """Запрос превью товара по ссылке (кнопка «применить» на форме хотелки)."""

    model_config = ConfigDict(
        json_schema_extra={
            'examples': [
                {'link': WB_LINK_EXAMPLE},
                {'link': OZON_LINK_EXAMPLE, 'html': '<html>…страница товара…</html>'},
            ]
        }
    )

    link: HttpUrl = Field(description='Ссылка на товар, как вставил юзер.')
    html: str | None = Field(
        default=None,
        description=(
            'HTML страницы товара, если клиент уже загрузил её сам (обход защиты '
            'магазина на устройстве). null/опущено — бэк ходит по ссылке сам.'
        ),
    )


class ItemInfoResponseSchema(BaseModel):
    """Превью товара для автозаполнения формы хотелки.

    Название/описание/картинка — как раньше. Цена (фича 0011) — только для
    поддерживаемого магазина и best-effort: её отсутствие не делает превью ошибкой.
    """

    model_config = ConfigDict(
        json_schema_extra={
            'examples': [
                {
                    'title': 'Кроссовки Nike Air',
                    'description': 'Беговые кроссовки',
                    'image_url': 'https://basket-10.wbbasket.ru/vol1666/part166652/166652374/images/big/1.webp',
                    'shop': 'wildberries',
                    'price': 4990,
                    'price_is_minimum': False,
                },
                {
                    'title': 'Платье летнее',
                    'description': 'Хлопок',
                    'image_url': 'https://basket-10.wbbasket.ru/vol1666/part166652/166652374/images/big/1.webp',
                    'shop': 'wildberries',
                    'price': 1990,
                    'price_is_minimum': True,
                },
                {
                    'title': 'Кроссовки Nike Air',
                    'description': 'Беговые кроссовки',
                    'image_url': 'https://basket-10.wbbasket.ru/vol1666/part166652/166652374/images/big/1.webp',
                    'shop': 'wildberries',
                    'price': None,
                    'price_is_minimum': False,
                },
                {
                    'title': 'Кофемолка',
                    'description': 'Ручная',
                    'image_url': 'https://cdn1.ozone.ru/s3/multimedia/123.jpg',
                    'shop': None,
                    'price': None,
                    'price_is_minimum': False,
                },
            ]
        }
    )

    title: str = Field(description='Название товара со страницы.')
    description: str = Field(
        description='Описание со страницы; может быть пустой строкой.'
    )
    image_url: HttpUrl = Field(description='Картинка товара (внешний URL).')
    shop: Shop | None = Field(
        description=(
            'Всегда присутствует. Магазин, распознанный по ссылке. null — не '
            'поддерживается: поле цены '
            'остаётся ручным, магазинных пометок в форме нет. Не null — поле цены '
            'помечайте как магазинное («с WB»); при сохранении с '
            '`price_edited = false` бэк возьмёт магазинную цену сам.'
        ),
        examples=['wildberries', None],
    )
    price: int | None = Field(
        description=(
            'Всегда присутствует. Текущая цена со скидкой, свежий запрос к магазину '
            '(не дольше 10 с), рубли. Подставьте в поле цены формы, чтобы юзер увидел '
            'её сразу. null — магазин не поддерживается (`shop = null`), товар '
            'распродан/исчез в этот момент, либо магазин цену не отдал/не ответил '
            'за 10 с: превью всё равно `200` (тихо), поле цены пустое; при '
            'сохранении с `price_edited = false` бэк попробует ещё раз, а дальше '
            'цену принесёт обход.'
        ),
        examples=[4990, None],
    )
    price_is_minimum: bool = Field(
        description=(
            'Всегда присутствует. true — `price` это минимум среди размеров в '
            'наличии (ссылка без `?size=` у многоразмерного товара): показывайте '
            'как «от». false — цена конкретного размера или цены нет.'
        ),
        examples=[False],
    )
