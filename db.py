from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, ForeignKey, String, create_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)
from sqlalchemy.types import DECIMAL

from config import settings


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'user'

    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(30), nullable=False)
    last_name: Mapped[str] = mapped_column(String(30), nullable=False)
    photo_url: Mapped[str] = mapped_column(String(200))

    vk_id: Mapped[str] = mapped_column(String(15), unique=True)
    vk_access_token: Mapped[str] = mapped_column(String(100), unique=True)
    firebase_uid: Mapped[Optional[str]] = mapped_column(
        String(100), unique=True, nullable=True
    )

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
    'sqlite:///db.sqlite',
    echo=settings.IS_DEBUG,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
