from datetime import date
from decimal import Decimal
from typing import Annotated, Optional, Union
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    FilePath,
    HttpUrl,
    field_validator,
)

from app.constants import Gender


class BaseWishSchema(BaseModel):
    name: str
    description: Optional[str]
    # price: Union[Annotated[Decimal, Field(decimal_places=2)], None]
    price: Optional[int]
    link: Optional[HttpUrl]


class WishWriteSchema(BaseWishSchema):
    pass


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
    user_id: UUID
    is_archived: bool
    reserved_by_id: Optional[UUID]
    image: Optional[str]
    user: OtherUserSchema

    @field_validator('image', mode='before')
    @staticmethod
    def make_image_url(image_name: str) -> Optional[str]:
        if not image_name:
            return None
        return f'/media/wish_images/{image_name}'


class AnnotatedOtherUserSchema(BaseUserSchema):
    model_config = ConfigDict(from_attributes=True)

    follows: list[OtherUserSchema]
    followed_by: list[OtherUserSchema]
    follows_me: bool
    followed_by_me: bool


class CurrentUserReadSchema(BaseUserSchema):
    phone: Optional[str]
    email: EmailStr | None
    follows: list[OtherUserSchema]
    followed_by: list[OtherUserSchema]


class CurrentUserUpdateSchema(BaseModel):
    display_name: str
    gender: Gender
    birth_date: Optional[date]


class RequestFirebaseAuthSchema(BaseModel):
    id_token: str


class SavePushTokenSchema(BaseModel):
    push_token: str


class RequestVkAuthMobileSchema(BaseModel):
    access_token: str
    email: EmailStr | None
    phone: str | None


class ResponseVkAuthWebSchema(BaseModel):
    vk_access_token: str
    firebase_uid: str
    firebase_token: str


class ResponseVkAuthMobileSchema(BaseModel):
    firebase_uid: str
    firebase_token: str


class ItemInfoRequestSchema(BaseModel):
    link: HttpUrl
    html: str | None


class ItemInfoResponseSchema(BaseModel):
    title: str
    description: str
    image_url: HttpUrl
