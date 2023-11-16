from pydantic import BaseModel
from decimal import Decimal
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from sqlalchemy import ForeignKey, String, create_engine
from sqlalchemy.types import DECIMAL
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    sessionmaker,
)
from schemas import WishCreateSchema


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'user'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(30))

    # wishes: Mapped[list['Wish']] = relationship(
    #     back_populates='user', cascade='all, delete-orphan'
    # )


class Wish(Base):
    __tablename__ = 'wish'

    # user_id: Mapped[int] = mapped_column(ForeignKey('user.id'))
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(String(1000))
    price: Mapped[Decimal] = mapped_column(DECIMAL(precision=2))

    # user: Mapped['User'] = relationship(back_populates='wishes')


engine = create_engine(
    'sqlite:///db.sqlite', echo=True, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(engine)


app = FastAPI()


@app.get('/')
def main():
    return RedirectResponse('/wishes')


@app.get('/wishes')
def my_wishes():
    with SessionLocal() as session:
        wishes = session.query(Wish).all()
    return [
        {
            'id': wish.id,
            'name': wish.name,
            'description': wish.description,
            'price': wish.price,
            'is_active': True,
        }
        for wish in wishes
    ]


@app.post('/wishes')
def add_wish(
    wish_data: WishCreateSchema,
):
    wish = Wish(
        name=wish_data.name,
        description=wish_data.description,
        price=wish_data.price,
    )
    with SessionLocal() as session:
        session.add(wish)
        session.commit()


@app.delete('/wishes/{wish_id}')
def delete(wish_id: int):
    with SessionLocal() as session:
        session.query(Wish).filter(id == wish_id).delete()
