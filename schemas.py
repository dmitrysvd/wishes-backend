from decimal import Decimal
from typing import Optional

from pydantic import AnyUrl, BaseModel


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


class PublicUserSchema(BaseModel):
    first_name: str
    last_name: str
    photo_url: AnyUrl


class PrivateUserSchema(BaseModel):
    first_name: str
    last_name: str
    photo_url: AnyUrl
    phone: str
    email: str


class RequestFirebaseAuthSchema(BaseModel):
    id_token: str
    push_token: str


class ResponseAuthSchema(BaseModel):
    access_token: str
    debug: Optional[str] = None
