from decimal import Decimal
from typing import Optional
from pydantic import BaseModel


class WishCreateSchema(BaseModel):
    name: str
    description: Optional[str]
    price: Decimal

    class Config:
        orm_mode = True


class WishUpdateSchema(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[Decimal] = None

    class Config:
        orm_mode = True