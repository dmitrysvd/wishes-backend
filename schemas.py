from decimal import Decimal
from typing import Optional

from pydantic import BaseModel


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


class UserSchema(BaseModel):
    name: str

    class Config:
        orm_mode = True