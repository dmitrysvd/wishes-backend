from datetime import date
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
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


class PublicUserSchema(BaseModel):
    """Публичные данные владельца вишлиста — без email/телефона и прочего PII."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_name: str
    photo_url: HttpUrl | None
    birth_date: date | None


class PublicWishSchema(BaseWishSchema):
    """Хотелка для публичной веб-страницы: без личности зарезервировавшего."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    image: str | None
    is_reserved: bool

    @field_validator('image', mode='before')
    @staticmethod
    def make_image_url(image_name: str) -> str | None:
        if not image_name:
            return None
        return f'/media/wish_images/{image_name}'


class PublicWishlistSchema(BaseModel):
    """Публичный вишлист: владелец + его активные хотелки."""

    user: PublicUserSchema
    wishes: list[PublicWishSchema]


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
