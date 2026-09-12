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
    Integer,
    MetaData,
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
from app.constants import (
    FollowAction,
    FollowSource,
    Gender,
    PriceObservationStatus,
    PriceRefreshOutcome,
    PriceSource,
    Shop,
    StoreAvailability,
)
from app.parsers import parse_wildberries_link

# Явные имена констрейнтов вместо тех, что придумывает Postgres. Без конвенции
# безымянные ограничения получают имя от БД, а alembic сличает их по имени —
# и autogenerate/check на них молча ненадёжен.
NAMING_CONVENTION = {
    'ix': 'ix_%(column_0_label)s',
    'uq': 'uq_%(table_name)s_%(column_0_name)s',
    'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


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
    CheckConstraint('follower_id <> followed_id', name='no_self_follow'),
)


class User(Base):
    __tablename__ = 'user'
    __table_args__ = (
        # «Нет токена» = NULL; пустая строка запрещена, чтобы не было второго
        # представления того же состояния (NULL проходит: NULL <> '' → unknown).
        CheckConstraint("firebase_push_token <> ''", name='push_token_not_empty'),
    )

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

    # True — сид-юзер dev/test-байпаса (фича 0009). Токен по секрету выдаётся и
    # принимается ТОЛЬКО для таких юзеров: даже утёкший секрет не даёт войти в
    # реальный аккаунт. Реальные пользователи всегда False.
    is_test: Mapped[bool] = mapped_column(
        default=False, server_default='false', nullable=False
    )

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
        CheckConstraint('user_id <> referrer_id', name='not_self_referral'),
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


# Внутренний статус наблюдения → публичный enum наличия (контракт 0011).
STORE_AVAILABILITY_BY_STATUS = {
    PriceObservationStatus.ok: StoreAvailability.in_stock,
    PriceObservationStatus.sold_out: StoreAvailability.sold_out,
    PriceObservationStatus.gone: StoreAvailability.gone,
}


class Wish(Base):
    __tablename__ = 'wish'
    __table_args__ = (
        CheckConstraint('user_id <> reserved_by_id', name='user_not_equal_reserved_by'),
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

    # Момент резервирования. Nullable: «не зарезервировано», а у резерваций,
    # сделанных до инструментации, реальная дата неизвестна (NULL = легаси).
    # Нужен, чтобы резерв можно было отнести ко времени: без него нельзя измерить,
    # даёт ли повод (бёрздей-радар, пуш) прирост подарков.
    reserved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    # Откуда `price` (фича 0011). При `shop` цену пишут превью/сохранение/кнопка
    # (свежий запрос) и суточный обход; при `manual` магазин её не трогает.
    price_source: Mapped[PriceSource] = mapped_column(
        Enum(PriceSource), nullable=False, default=PriceSource.manual
    )
    # `price` — минимум среди размеров в наличии (ссылка без `?size=`): UI рисует
    # «от». Меняется только вместе с `price`; у ручной цены всегда False.
    price_is_minimum: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=False
    )
    # Последнее наблюдение магазина, денормализованное на хотелку, чтобы списки
    # не ходили в историю наблюдений за каждой строкой. NULL — магазин по этой
    # ссылке ещё ни разу не отвечал. Обновляется только у `shop`-хотелок; у
    # `manual` замирает и наружу не отдаётся (см. `store_observation`).
    store_availability: Mapped[PriceObservationStatus | None] = mapped_column(
        Enum(PriceObservationStatus), nullable=True
    )
    store_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    @property
    def shop(self) -> Shop | None:
        """Магазин по ссылке — правило «что такое WB-ссылка» живёт в парсере."""
        if self.link and parse_wildberries_link(self.link) is not None:
            return Shop.wildberries
        return None

    @property
    def store_observation(self) -> dict[str, Any] | None:
        """`StoreObservationSchema` для контракта: только у магазинной цены и
        только если магазин уже отвечал. У `manual` магазин молчит — None."""
        if (
            self.price_source != PriceSource.shop
            or self.store_availability is None
            or self.store_observed_at is None
        ):
            return None
        return {
            'availability': STORE_AVAILABILITY_BY_STATUS[self.store_availability],
            'observed_at': self.store_observed_at,
        }

    @classmethod
    def get_active_wish_query(cls):
        return select(cls).where(~cls.is_archived)


class PushReason(enum.Enum):
    CURRENT_USER_BIRTHDAY = enum.auto()
    FOLLOWER_BIRTHDAY = enum.auto()
    # Сезонный глобальный повод (НГ/8 марта/…) — не зависит от follow-графа.
    SEASONAL = enum.auto()
    EMPTY_LIST_REACTIVATION = enum.auto()


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
    # Ключ дедупа сезонной кампании вида `mar8-2026`. Для не-сезонных пушей пуст.
    campaign_key: Mapped[str | None] = mapped_column(String(64), nullable=True)


class WishPriceRefreshEvent(Base):
    """Append-only лог нажатий «актуальная с WB» (фича 0011).

    Критерий приёмки фичи — счётчик нажатий как мера того, нужен ли ручной
    режим вообще. Считаем каждое нажатие, включая неудачные (`outcome`), чтобы
    отличить «кнопка не нужна» от «кнопка не работает».
    """

    __tablename__ = 'wish_price_refresh_event'

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    wish_id: Mapped[UUID] = mapped_column(
        ForeignKey('wish.id', ondelete='CASCADE'), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey('user.id', ondelete='CASCADE'), nullable=False
    )
    outcome: Mapped[PriceRefreshOutcome] = mapped_column(
        Enum(PriceRefreshOutcome), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


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


class UserActivityDay(Base):
    """Суточный след активности юзера — прибор для измерения возврата.

    `User.last_login_at` хранит только ПОСЛЕДНИЙ вход и затирается при каждом
    следующем: по нему нельзя ни посчитать честный DAU/WAU/MAU, ни увидеть,
    вернулся ли человек к следующему поводу (свой ДР, ДР друга, НГ) — а возврат
    у продукта событийный, и мерить его нужно на горизонте 6–12 месяцев.

    Здесь на юзера копится по одной строке в сутки (upsert по составному
    ключу), поэтому история возвратов не теряется, а запись остаётся дешёвой:
    не больше одной строки на юзера в день, независимо от числа запросов.

    `radar_open_count` отделяет «просто зашёл» от «открыл бёрздей-радар» —
    без этого работу фичи 0007 не отделить от фона.

    Сутки считаем в UTC (как и все остальные метки времени в проекте).
    """

    __tablename__ = 'user_activity_day'

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey('user.id', ondelete='CASCADE'), primary_key=True
    )
    activity_date: Mapped[date] = mapped_column(Date(), primary_key=True, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Число авторизованных запросов за сутки — грубая глубина визита.
    request_count: Mapped[int] = mapped_column(
        Integer(), server_default='0', nullable=False
    )
    # Из них — открытий бёрздей-радара (GET /birthday_radar).
    radar_open_count: Mapped[int] = mapped_column(
        Integer(), server_default='0', nullable=False
    )


class WishPriceObservation(Base):
    """Суточное наблюдение цены и наличия товара по ссылке хотелки — прибор 0010.

    `Wish.price` хранит один снимок на момент добавления, истории нет — по нему
    нельзя понять, дешевеют ли отложенные вещи и пропадают ли из наличия. Здесь
    копится append-only ряд: одна строка на хотелку в сутки (UTC), повтор обхода
    за те же сутки не перетирает первое наблюдение (`ON CONFLICT DO NOTHING`).

    Дырка в ряду (обход упал) — это ОТСУТСТВИЕ строки; «распродано»/«исчез» — это
    строка со статусом. Два механизма, не путать.

    Идентичность товара `(shop, sku, size_option_id)` лежит в каждой строке, а не
    на хотелке: юзер может сменить ссылку, и тогда ряд по хотелке распадётся на два
    товара. История группируется по `(wish_id, shop, sku, size_option_id)`; хук на
    редактирование хотелки не нужен, прибор не трогает пользовательский код.

    У WB `sku` — это артикул карточки (`nm`: модель в одном цвете), а единица
    остатка — размер внутри неё (`sizes[].optionId`), даже у безразмерных товаров
    (один размер с пустым именем). Две трети ссылок несут `?size=` — тогда
    наблюдаем именно этот размер. Без размера у многоразмерного товара берём
    минимальную `product`-цену среди размеров в наличии; если в наличии нет ни
    одного — `sold_out`.

    Обе цены WB: `basic` — до скидки, `product` — со скидкой; какая из них «цена»
    для продукта — решается на этапе фичи (🟡 Q4 intent'а).
    """

    __tablename__ = 'wish_price_observation'
    __table_args__ = (
        # Цены есть тогда и только тогда, когда товар в наличии. Связка держится
        # в схеме, а не в коде, чтобы статус и цены не разошлись.
        CheckConstraint(
            "(status = 'ok') = (basic_price IS NOT NULL AND product_price IS NOT NULL)",
            name='prices_iff_ok',
        ),
    )

    wish_id: Mapped[UUID] = mapped_column(
        ForeignKey('wish.id', ondelete='CASCADE'), primary_key=True
    )
    observed_date: Mapped[date] = mapped_column(Date(), primary_key=True)
    shop: Mapped[Shop] = mapped_column(Enum(Shop), nullable=False)
    sku: Mapped[int] = mapped_column(Integer(), nullable=False)
    # Размер из `?size=` ссылки; NULL — размер в ссылке не указан.
    size_option_id: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    status: Mapped[PriceObservationStatus] = mapped_column(
        Enum(PriceObservationStatus), nullable=False
    )
    basic_price: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=10, scale=2), nullable=True
    )
    product_price: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=10, scale=2), nullable=True
    )
    # Односторонняя связь для админки (ссылка на хотелку). Обратной коллекции на
    # Wish нет: обход пишет через pg_insert, а ленивая загрузка тысяч наблюдений
    # на хотелке никому не нужна.
    wish: Mapped['Wish'] = relationship()


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
