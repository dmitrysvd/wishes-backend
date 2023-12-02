from decimal import Decimal
from typing import Annotated, Optional, Union

from pydantic import AnyUrl, BaseModel, EmailStr, Field


class BaseWishSchema(BaseModel):
    name: str
    description: Optional[str]
    # price: Union[Annotated[Decimal, Field(decimal_places=2)], None]
    price: Optional[int]


class WishReadSchema(BaseWishSchema):
    id: int
    user_id: int
    is_active: bool


class WishWriteSchema(BaseWishSchema):
    pass


class BaseUserSchema(BaseModel):
    id: int
    display_name: str
    photo_url: AnyUrl
    wishes: list[WishReadSchema]


class OtherUserSchema(BaseUserSchema):
    pass


class CurrentUserSchema(BaseUserSchema):
    phone: Optional[str]
    email: EmailStr
    reserved_wishes: list[WishReadSchema]
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
