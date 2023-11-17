from decimal import Decimal

from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse
from sqlalchemy import Boolean, ForeignKey, String, create_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)
from sqlalchemy.types import DECIMAL

from schemas import UserSchema, WishReadSchema, WishWriteSchema


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'user'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(30), nullable=False)

    wishes: Mapped[list['Wish']] = relationship(
        back_populates='user', cascade='all, delete-orphan'
    )


class Wish(Base):
    __tablename__ = 'wish'

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user.id'))
    name: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(String(1000), nullable=True)
    price: Mapped[Decimal] = mapped_column(DECIMAL(precision=2), nullable=True)
    is_active: Mapped[Boolean] = mapped_column(Boolean(), default=False)

    user: Mapped['User'] = relationship(back_populates='wishes')


engine = create_engine(
    'sqlite:///db.sqlite', echo=True, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(engine)

app = FastAPI()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(db: Session) -> User:
    user = db.query(User).first()
    if not user:
        raise Exception
    return user


@app.get('/')
def main():
    return RedirectResponse('/wishes')


@app.get('/wishes')
def my_wishes() -> list[WishReadSchema]:
    with SessionLocal() as session:
        current_user = get_current_user(session)
        wishes = session.query(Wish).filter(Wish.user == current_user)
    return [
        WishReadSchema.model_validate(wish, from_attributes=True) for wish in wishes
    ]


@app.get('/wishes/user/{user_id}')
def user_wishes(user_id: int) -> list[WishReadSchema]:
    with SessionLocal() as session:
        user = session.query(User).get(user_id)
        wishes = session.query(Wish).filter(Wish.user == user)
    return [
        WishReadSchema.model_validate(wish, from_attributes=True) for wish in wishes
    ]


@app.post('/wishes')
def add_wish(
    wish_data: WishWriteSchema,
):
    with SessionLocal() as session:
        user = session.query(User).first()
        if user is None:
            raise Exception
        wish = Wish(
            user_id=user.id,
            name=wish_data.name,
            description=wish_data.description,
            price=wish_data.price,
        )
        session.add(wish)
        session.commit()


@app.put('/wishes/{wish_id}')
def update_wish(
    wish_id: int,
    wish_data: WishReadSchema,
):
    with SessionLocal() as session:
        wish = session.query(Wish).get(wish_id)
        if not wish:
            raise Exception
        wish.name = wish_data.name
        wish.description = wish_data.description
        wish.price = wish_data.price
        session.add(wish)
        session.commit()


@app.delete('/wishes/{wish_id}')
def delete_wish(wish_id: int):
    with SessionLocal() as session:
        session.query(Wish).filter(id == wish_id).delete()


@app.post('/register/')
def register_user(user: UserSchema, db: Session = Depends(get_db)):
    with SessionLocal() as session:
        db_user = User(**user.model_dump())
        session.add(db_user)
        session.commit()


@app.get('/users/')
def users() -> list[UserSchema]:
    with SessionLocal() as session:
        db_users = session.query(User).all()
    return [
        UserSchema.model_validate(db_user, from_attributes=True) for db_user in db_users
    ]
