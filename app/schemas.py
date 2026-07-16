from datetime import date
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

from app.constants import Gender

ItemT = TypeVar('ItemT', bound=BaseModel)


class PageSchema(BaseModel, Generic[ItemT]):
    """Универсальная схема-страница для offset/limit-пагинации."""

    items: list[ItemT]
    total: int
    has_next: bool
    has_previous: bool


class BaseWishSchema(BaseModel):
    name: str
    description: str | None
    price: int | None
    link: HttpUrl | None


class WishWriteSchema(BaseWishSchema):
    recommendation_id: UUID | None = None


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

    email: EmailStr | None


class WishReadSchema(BaseWishSchema):
    id: UUID
    is_archived: bool
    reserved_by_id: UUID | None
    image: str | None
    recommendation_id: UUID | None
    user: OtherUserSchema

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
        description='Ориентировочная цена, целое число рублей. null — цена не указана.',
        examples=[3500],
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
                            'link': 'https://www.ozon.ru/product/123456',
                            'image_url': '/media/wish_images/ab12cd34.jpg',
                            'is_reserved': False,
                        },
                        {
                            'id': '9b2d5e4a-1c3f-4a2b-8d6e-0f1a2b3c4d5e',
                            'name': 'Книга «Дюна»',
                            'description': None,
                            'price': None,
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
    push_token: str


class RequestVkAuthWebSchema(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            'examples': [
                {
                    'silent_token': 'vk-silent-token',
                    'uuid': 'vk-uuid',
                    'attribution': {
                        'referrer_id': '7c9e6679-7425-40de-944b-e07fc1f90ae7',
                        'utm_source': 'vk',
                    },
                },
                {'silent_token': 'vk-silent-token', 'uuid': 'vk-uuid'},
            ]
        }
    )

    silent_token: str
    uuid: str
    attribution: RegistrationAttributionSchema | None = Field(
        default=None,
        description=(
            'Атрибуция установки/реферала, учитывается только при создании нового '
            'юзера. Опущено/`null` = без атрибуции.'
        ),
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


class RequestVkAuthAndroidSchema(BaseModel):
    """Вход через VK ID SDK на Android (Confidential Flow, OAuth 2.1).

    SDK на устройстве проводит авторизацию (per-request PKCE и `state` — внутри
    SDK) и отдаёт клиенту **authorization code**, а не готовый токен. Клиент
    пересылает `code` бэку, и обмен `code → access_token` идёт **на сервере**.
    Почему не токен напрямую (как в легаси `/auth/vk/mobile`): в Public Flow VK
    привязывает `access_token` к IP телефона, и серверная валидация с IP
    датацентра невозможна; Confidential Flow привязывает токен к IP бэка, который
    его и использует. `client_secret` при этом не покидает сервер.

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
                    'attribution': {
                        'referrer_id': '7c9e6679-7425-40de-944b-e07fc1f90ae7',
                        'utm_source': 'whatsapp',
                    },
                },
                {
                    'code': 'vk1.a.authorization-code-from-sdk',
                    'code_verifier': 'dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk',
                    'device_id': 'vk-device-id-from-sdk',
                },
            ]
        }
    )

    code: str = Field(
        description=(
            'Одноразовый authorization code из VK ID SDK '
            '(`ConfidentialFlowData.code`). Бэк обменивает его на токены у VK ID '
            'Backend. Повторный обмен уже использованного/истёкшего `code` → `401`.'
        )
    )
    code_verifier: str = Field(
        description=(
            'PKCE `code_verifier`, сгенерированный SDK на устройстве под этот '
            '`code`. Бэк передаёт его в обмене; VK сверяет с `code_challenge` из '
            'шага авторизации. Несовпадение → `401`.'
        )
    )
    device_id: str = Field(
        description=(
            'Идентификатор устройства из VK ID SDK '
            '(`ConfidentialFlowData.deviceId`). Требуется VK ID при обмене кода.'
        )
    )
    attribution: RegistrationAttributionSchema | None = Field(
        default=None,
        description=(
            'Атрибуция установки/реферала, учитывается только при создании нового '
            'юзера. Опущено/`null` = без атрибуции.'
        ),
    )


class ResponseVkAuthWebSchema(BaseModel):
    vk_access_token: str
    firebase_uid: str
    firebase_token: str
    user_created: bool


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


class ItemInfoRequestSchema(BaseModel):
    link: HttpUrl
    html: str | None = None


class ItemInfoResponseSchema(BaseModel):
    title: str
    description: str
    image_url: HttpUrl
