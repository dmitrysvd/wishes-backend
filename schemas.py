from decimal import Decimal
from pydantic import BaseModel


class WishCreateSchema(BaseModel):
    name: str
    description: str|None
    price: Decimal

    class Config:
        orm_mode = True