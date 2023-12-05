from datetime import date
from decimal import Decimal
from typing import Annotated, Optional, Union
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator

from constants import Gender


class BaseWishSchema(BaseModel):
    name: str
    description: Optional[str]
    # price: Union[Annotated[Decimal, Field(decimal_places=2)], None]
    price: Optional[int]
    link: Optional[HttpUrl]
    image: Optional[str]

    @field_validator('image', mode='before')
    @staticmethod
    def make_image_url(image_name: str):
        return f'/media/wish_images/{image_name}'


class WishReadSchema(BaseWishSchema):
    id: UUID
    user_id: UUID
    is_active: bool


class WishWriteSchema(BaseWishSchema):
    pass


class BaseUserSchema(BaseModel):
    id: UUID
    display_name: str
    photo_url: HttpUrl


class OtherUserSchema(BaseUserSchema):
    pass


class CurrentUserSchema(BaseUserSchema):
    phone: Optional[str]
    email: EmailStr
    gender: Gender
    birth_date: Optional[date]
    follows: list['OtherUserSchema']
    followed_by: list['OtherUserSchema']


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
