import enum
from datetime import date, datetime
from decimal import Decimal
from sqlite3 import Connection as SQLite3Connection
from typing import Any
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
    Numeric,
    String,
    Table,
    Text,
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

from app.config import settings
from app.constants import FollowAction, FollowSource, Gender


class Base(DeclarativeBase):
    pass


user_following_table = Table(
    'user_following',
    Base.metadata,
    Column('follower_id', ForeignKey('user.id', ondelete='CASCADE'), primary_key=True),
    Column('followed_id', ForeignKey('user.id', ondelete='CASCADE'), primary_key=True),
    # Время создания подписки. Nullable: у рёбер, созданных до инструментации,
    # реальная дата неизвестна (NULL = легаси), новые проставляются server_default.
    Column(
        'created_at',
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=True,
    ),
    CheckConstraint('follower_id <> followed_id'),
)


class User(Base):
    __tablename__ = 'user'

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    display_name: Mapped[str] = mapped_column(String(250), nullable=False)
    email: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    phone: Mapped[str | None] = mapped_column(String(15))
    birth_date: Mapped[date | None] = mapped_column(Date())
    gender: Mapped[Gender | None] = mapped_column(Enum(Gender))
    photo_url: Mapped[str | None] = mapped_column(String(1024))
    photo_path: Mapped[str | None] = mapped_column(String(200))
    # True — фото загружено пользователем вручную; такое не перетираем
    # соц-аватаркой (бэкфилл на диск, будущий refresh-на-логине).
    photo_is_custom: Mapped[bool] = mapped_column(
        default=False, server_default='false', nullable=False
    )

    vk_id: Mapped[str | None] = mapped_column(String(15), unique=True)
    vk_access_token: Mapped[str | None] = mapped_column(
        String(500),
        unique=True,
    )
    vk_friends_data: Mapped[list[Any] | None] = mapped_column(JSON)
    firebase_uid: Mapped[str] = mapped_column(String(1000), unique=True)
    firebase_push_token: Mapped[str | None] = mapped_column(String(1000))
    firebase_push_token_saved_at: Mapped[datetime | None] = mapped_column()

    registered_at: Mapped[datetime] = mapped_column(nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column()

    pre_bday_push_for_followers_last_sent_at: Mapped[datetime | None] = mapped_column()

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


class UserAttribution(Base):
    """First-touch атрибуция регистрации: кто привёл нового юзера и через какой
    канал он установил приложение (фича 0003).

    Ставится один раз при создании юзера и далее неизменна. Вынесена в отдельную
    таблицу (1:1 к `user`), чтобы поверх неё можно было наращивать будущие фичи
    (пуш пригласившему, авто-подписка, «вас пригласил X») без раздувания `user`.
    Строка пишется, только если есть что зафиксировать: валидный реферер и/или
    канал; чистый органик (обе метки пусты) строки не создаёт.
    """

    __tablename__ = 'user_attribution'
    __table_args__ = (
        CheckConstraint('user_id <> referrer_id', name='attribution_not_self_referral'),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    # Кого атрибутируем — новый юзер. 1:1, поэтому unique.
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey('user.id', ondelete='CASCADE'), unique=True, nullable=False
    )
    # Кто привёл (владелец инвайт-ссылки). NULL = органик/канальный вход без реферера.
    # При удалении реферера метку не теряем — обнуляем ссылку.
    referrer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey('user.id', ondelete='SET NULL'), nullable=True
    )
    # Канал установки (свободная строка от клиента), усечён до UTM_SOURCE_MAX_LENGTH.
    utm_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped['User'] = relationship(foreign_keys=[user_id])
    referrer: Mapped['User | None'] = relationship(foreign_keys=[referrer_id])


class WishRecommendation(Base):
    __tablename__ = 'wish_recommendation'

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(250))
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=10, scale=2), nullable=True
    )
    link: Mapped[str] = mapped_column(String(500))
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    wishes: Mapped[list['Wish']] = relationship(back_populates='recommendation')


class Wish(Base):
    __tablename__ = 'wish'
    __table_args__ = (
        CheckConstraint(
            'user_id <> reserved_by_id', name='wish_user_not_equal_reserved_by'
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('user.id'))
    reserved_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey('user.id'), nullable=True
    )
    name: Mapped[str] = mapped_column(String(250))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=10, scale=2), nullable=True
    )
    image: Mapped[str | None] = mapped_column(String(500), nullable=True)
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

    recommendation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey('wish_recommendation.id'), nullable=True
    )

    user: Mapped['User'] = relationship(back_populates='wishes', foreign_keys=[user_id])
    reserved_by: Mapped['User | None'] = relationship(
        back_populates='reserved_wishes', foreign_keys=[reserved_by_id]
    )
    recommendation: Mapped['WishRecommendation | None'] = relationship(
        back_populates='wishes'
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

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    sent_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False)
    reason_user_id: Mapped[UUID] = mapped_column(
        ForeignKey('user.id', ondelete='CASCADE'), nullable=False
    )
    target_user_id: Mapped[UUID] = mapped_column(
        ForeignKey('user.id', ondelete='CASCADE'), nullable=False
    )
    reason: Mapped[PushReason] = mapped_column(Enum(PushReason))


class FollowEvent(Base):
    """Append-only лог событий подписки — инструментация follow-графа.

    В отличие от таблицы рёбер `user_following` (хранит только текущее состояние
    и теряет строку при отписке), лог копит и follow, и unfollow во времени —
    это даёт динамику графа и сигнал оттока связей. `source` проставляет клиент.
    """

    __tablename__ = 'follow_event'

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey('user.id', ondelete='CASCADE'), nullable=False
    )
    target_id: Mapped[UUID] = mapped_column(
        ForeignKey('user.id', ondelete='CASCADE'), nullable=False
    )
    action: Mapped[FollowAction] = mapped_column(Enum(FollowAction), nullable=False)
    source: Mapped[FollowSource | None] = mapped_column(
        Enum(FollowSource), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.IS_DEBUG,
    # connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@event.listens_for(Engine, 'connect')
def do_connect(dbapi_connection, connection_record):
    if not isinstance(dbapi_connection, SQLite3Connection):
        # для postgres не выполняем
        return

    # disable pysqlite's emitting of the BEGIN statement entirely.
    # also stops it from emitting COMMIT before any DDL.
    dbapi_connection.isolation_level = None

    # enable FK constraints
    cursor = dbapi_connection.cursor()
    cursor.execute('PRAGMA foreign_keys=ON;')
    cursor.close()


@event.listens_for(engine, 'begin')
def do_begin(conn):
    # emit our own BEGIN
    conn.exec_driver_sql('BEGIN')
