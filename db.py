from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    String,
    Table,
    create_engine,
)
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


user_following_table = Table(
    'user_following',
    Base.metadata,
    Column('follower_id', ForeignKey('user.id', ondelete='CASCADE'), primary_key=True),
    Column('followed_id', ForeignKey('user.id', ondelete='CASCADE'), primary_key=True),
    CheckConstraint('follower_id <> followed_id'),
)


class User(Base):
    __tablename__ = 'user'

    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(30), nullable=True)
    last_name: Mapped[str] = mapped_column(String(30), nullable=True)
    display_name: Mapped[str] = mapped_column(String(50), nullable=False)
    phone: Mapped[str] = mapped_column(String(15), nullable=True)
    email: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    photo_url: Mapped[str] = mapped_column(String(200))

    vk_id: Mapped[Optional[str]] = mapped_column(String(15), unique=False)
    vk_access_token: Mapped[Optional[str]] = mapped_column(String(100), unique=False)
    firebase_uid: Mapped[Optional[str]] = mapped_column(
        String(100), unique=True, nullable=True
    )
    firebase_push_token: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )

    wishes: Mapped[list['Wish']] = relationship(
        back_populates='user', cascade='all, delete-orphan'
    )
    follows: Mapped[list['User']] = relationship(
        secondary=user_following_table,
        primaryjoin=(id == user_following_table.c.follower_id),
        secondaryjoin=(id == user_following_table.c.followed_id),
        back_populates='followed_by',
    )
    followed_by: Mapped[list['User']] = relationship(
        secondary=user_following_table,
        primaryjoin=(id == user_following_table.c.followed_id),
        secondaryjoin=(id == user_following_table.c.follower_id),
        back_populates='follows',
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
