from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Enum,
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
from constants import Gender


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
    display_name: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    phone: Mapped[str] = mapped_column(String(15), nullable=True)
    gender: Mapped[Gender] = mapped_column(Enum(Gender))
    photo_url: Mapped[str] = mapped_column(String(200))

    vk_id: Mapped[str] = mapped_column(String(15), unique=True, nullable=False)
    vk_access_token: Mapped[str] = mapped_column(
        String(500),
        unique=True,
        nullable=False,
    )
    vk_friends_data: Mapped[Any] = mapped_column(JSON, nullable=False)
    firebase_uid: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    firebase_push_token: Mapped[str] = mapped_column(String(100), nullable=True)

    wishes: Mapped[list['Wish']] = relationship(
        back_populates='user',
        cascade='all, delete-orphan',
        foreign_keys='[Wish.user_id]',
    )
    reserved_wishes: Mapped[list['Wish']] = relationship(
        back_populates='reserved_by',
        foreign_keys='Wish.reserved_by_id',
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

    def __str__(self) -> str:
        return f'User(id={self.id}, display_name="{self.display_name}")'


class Wish(Base):
    __tablename__ = 'wish'
    __table_args__ = (
        CheckConstraint(
            'user_id <> reserved_by_id', name='wish_user_not_equal_reserved_by'
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user.id'))
    reserved_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey('user.id'), nullable=True
    )
    name: Mapped[str] = mapped_column(String(50))
    description: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    price: Mapped[Optional[Decimal]] = mapped_column(
        DECIMAL(precision=2), nullable=True
    )
    is_active: Mapped[Boolean] = mapped_column(Boolean(), default=False)

    user: Mapped['User'] = relationship(back_populates='wishes', foreign_keys=[user_id])
    reserved_by: Mapped[Optional['User']] = relationship(
        back_populates='reserved_wishes', foreign_keys=[reserved_by_id]
    )

    def __str__(self) -> str:
        return f'Wish(id={self.id}, name="{self.name}")'


engine = create_engine(
    'sqlite:///db.sqlite',
    echo=settings.IS_DEBUG,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
