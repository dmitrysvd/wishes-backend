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


class RequestFirebaseAuthSchema(BaseModel):
    id_token: str


class SavePushTokenSchema(BaseModel):
    push_token: str


class RequestVkAuthWebSchema(BaseModel):
    silent_token: str
    uuid: str


class RequestVkAuthMobileSchema(BaseModel):
    access_token: str
    email: str | None
    phone: str | None


class ResponseVkAuthWebSchema(BaseModel):
    vk_access_token: str
    firebase_uid: str
    firebase_token: str
    user_created: bool


class ResponseVkAuthMobileSchema(BaseModel):
    firebase_uid: str
    firebase_token: str
    user_created: bool


class ItemInfoRequestSchema(BaseModel):
    link: HttpUrl
    html: str | None = None


class ItemInfoResponseSchema(BaseModel):
    title: str
    description: str
    image_url: HttpUrl
