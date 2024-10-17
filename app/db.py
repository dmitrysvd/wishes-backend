import enum
from datetime import date, datetime
from decimal import Decimal
from sqlite3 import Connection as SQLite3Connection
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Table,
    Uuid,
    create_engine,
    event,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)
from sqlalchemy.sql import func
from sqlalchemy.types import DECIMAL

from app.config import settings
from app.constants import Gender


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

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    display_name: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    phone: Mapped[Optional[str]] = mapped_column(String(15))
    birth_date: Mapped[Optional[date]] = mapped_column(Date())
    gender: Mapped[Optional[Gender]] = mapped_column(Enum(Gender))
    photo_url: Mapped[Optional[str]] = mapped_column(String(200))
    photo_path: Mapped[Optional[str]] = mapped_column(String(200))

    vk_id: Mapped[Optional[str]] = mapped_column(String(15), unique=True)
    vk_access_token: Mapped[Optional[str]] = mapped_column(
        String(500),
        unique=True,
    )
    vk_friends_data: Mapped[Optional[list[Any]]] = mapped_column(JSON)
    firebase_uid: Mapped[str] = mapped_column(String(100), unique=True)
    firebase_push_token: Mapped[Optional[str]] = mapped_column(String(100))
    firebase_push_token_saved_at: Mapped[datetime | None] = mapped_column()

    registered_at: Mapped[datetime] = mapped_column(nullable=False)

    # relationships
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

    def __repr__(self) -> str:
        return f'User(id={self.id}, display_name="{self.display_name}")'

    def __str__(self) -> str:
        return repr(self)


class Wish(Base):
    __tablename__ = 'wish'
    __table_args__ = (
        CheckConstraint(
            'user_id <> reserved_by_id', name='wish_user_not_equal_reserved_by'
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('user.id'))
    reserved_by_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey('user.id'), nullable=True
    )
    name: Mapped[str] = mapped_column(String(50))
    description: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    price: Mapped[Optional[Decimal]] = mapped_column(
        DECIMAL(precision=2), nullable=True
    )
    image: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean(), default=True)  # TODO: убрать
    is_archived: Mapped[bool] = mapped_column(Boolean(), default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    is_reservation_notification_sent: Mapped[bool] = mapped_column(
        default=False, nullable=False
    )
    is_creation_notification_sent: Mapped[bool] = mapped_column(
        default=False, nullable=False
    )

    user: Mapped['User'] = relationship(back_populates='wishes', foreign_keys=[user_id])
    reserved_by: Mapped[Optional['User']] = relationship(
        back_populates='reserved_wishes', foreign_keys=[reserved_by_id]
    )

    def __str__(self) -> str:
        return f'Wish(id={self.id}, name="{self.name}")'

    @property
    def is_reserved(self) -> bool:
        return bool(self.reserved_by_id)

    @classmethod
    def get_active_wish_query(cls):
        return select(cls).where(~cls.is_archived)


class PushReason(enum.Enum):
    CURRENT_USER_BIRTHDAY = enum.auto()
    FOLLOWER_BIRTHDAY = enum.auto()


class PushSendingLog(Base):
    __tablename__ = 'push_sending_log'

    id: Mapped[int] = mapped_column(Integer(), primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    reason_user_id: Mapped[UUID] = mapped_column(
        ForeignKey('user.id', ondelete='CASCADE'), nullable=False
    )
    target_user_id: Mapped[UUID] = mapped_column(
        ForeignKey('user.id', ondelete='CASCADE'), nullable=False
    )
    reason: Mapped[PushReason] = mapped_column(Enum(PushReason))


engine = create_engine(
    'sqlite:///db.sqlite',
    echo=settings.IS_DEBUG,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@event.listens_for(Engine, "connect")
def do_connect(dbapi_connection, connection_record):
    if not isinstance(dbapi_connection, SQLite3Connection):
        raise Exception('Not supported')

    # disable pysqlite's emitting of the BEGIN statement entirely.
    # also stops it from emitting COMMIT before any DDL.
    dbapi_connection.isolation_level = None

    # enable FK constraints and WAL mode
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.execute('PRAGMA journal_mode=WAL;')
    cursor.close()


@event.listens_for(engine, "begin")
def do_begin(conn):
    # emit our own BEGIN
    conn.exec_driver_sql("BEGIN")
