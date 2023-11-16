from decimal import Decimal
from pydantic import BaseModel


class WishCreateSchema(BaseModel):
    name: str
    description: str|None
    price: Decimal

    class Config:
        orm_mode = True


class WishUpdateSchema(BaseModel):
    name: str|None = None
    description: str|None = None
    price: Decimal|None = None

    class Config:
        orm_mode = True