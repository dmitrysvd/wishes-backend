from datetime import date
from decimal import Decimal
from typing import Annotated, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator

from app.constants import Gender


class BaseWishSchema(BaseModel):
    name: str
    description: Optional[str]
    # price: Union[Annotated[Decimal, Field(decimal_places=2)], None]
    price: Optional[int]
    link: Optional[HttpUrl]


class WishReadSchema(BaseWishSchema):
    id: UUID
    user_id: UUID
    is_active: bool
    reserved_by_id: Optional[UUID]
    image: Optional[str]

    @field_validator('image', mode='before')
    @staticmethod
    def make_image_url(image_name: str) -> Optional[str]:
        if not image_name:
            return None
        return f'/media/wish_images/{image_name}'


class WishWriteSchema(BaseWishSchema):
    pass


class BaseUserSchema(BaseModel):
    id: UUID
    display_name: str
    photo_url: HttpUrl
    gender: Gender
    birth_date: Optional[date]


class OtherUserSchema(BaseUserSchema):
    pass


class AnnotatedOtherUserSchema(BaseUserSchema):
    model_config = ConfigDict(from_attributes=True)

    follows_me: bool
    followed_by_me: bool


class CurrentUserReadSchema(BaseUserSchema):
    phone: Optional[str]
    email: EmailStr


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
    email: EmailStr
    phone: Optional[str]


class ResponseVkAuthWebSchema(BaseModel):
    vk_access_token: str
    firebase_uid: str
    firebase_token: str


class ResponseVkAuthMobileSchema(BaseModel):
    firebase_uid: str
    firebase_token: str


class ItemInfoSchema(BaseModel):
    title: str
    description: str
    image_url: HttpUrl
