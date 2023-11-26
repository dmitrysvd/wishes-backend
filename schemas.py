from decimal import Decimal
from typing import Optional

from pydantic import AnyUrl, BaseModel, EmailStr


class WishReadSchema(BaseModel):
    id: int
    user_id: int
    name: str
    description: Optional[str]
    price: Optional[Decimal]
    is_active: bool


class WishWriteSchema(BaseModel):
    name: str
    description: Optional[str]
    price: Optional[Decimal] = None


class OtherUserSchema(BaseModel):
    id: int
    # first_name: Optional[str]
    # last_name: Optional[str]
    display_name: str
    photo_url: AnyUrl


class CurrentUserSchema(BaseModel):
    id: int
    # first_name: Optional[str]
    # last_name: Optional[str]
    display_name: str
    photo_url: AnyUrl
    phone: Optional[str]
    email: EmailStr
    follows: list['OtherUserSchema']
    followed_by: list['OtherUserSchema']


class RequestFirebaseAuthSchema(BaseModel):
    id_token: str


class SavePushTokenSchema(BaseModel):
    push_token: str


class VkAuthViaSilentTokenSchema(BaseModel):
    uuid: str
    silent_token: str


class ResponseAuthSchema(BaseModel):
    vk_access_token: str
    firebase_uid: str
    firebase_token: str
