"""wish_price_observation: прибор цен и наличия (фича 0010)

`wish.price` — один снимок на момент добавления, истории нет. Заводим суточный
append-only ряд наблюдений по хотелкам со ссылкой на поддерживаемый магазин:
одна строка на хотелку в сутки, три статуса (в наличии / распродан / исчез),
обе цены WB (до и со скидкой). Идентичность товара `(shop, sku, size_option_id)`
— в строке, а не на хотелке, чтобы смена ссылки не требовала ни хука, ни
удаления истории.

Revision ID: c7e2a9d4f1b3
Revises: e3b0c44298fc
Create Date: 2026-09-10 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c7e2a9d4f1b3'
down_revision: str | None = 'e3b0c44298fc'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

shop = sa.Enum('wildberries', name='shop')
price_observation_status = sa.Enum(
    'ok', 'sold_out', 'gone', name='priceobservationstatus'
)


def upgrade() -> None:
    op.create_table(
        'wish_price_observation',
        sa.Column('wish_id', sa.Uuid(), nullable=False),
        sa.Column('observed_date', sa.Date(), nullable=False),
        sa.Column('shop', shop, nullable=False),
        sa.Column('sku', sa.Integer(), nullable=False),
        sa.Column('size_option_id', sa.Integer(), nullable=True),
        sa.Column('status', price_observation_status, nullable=False),
        sa.Column('basic_price', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('product_price', sa.Numeric(precision=10, scale=2), nullable=True),
        # Цены есть ⇔ товар в наличии — инвариант держит БД, а не код.
        sa.CheckConstraint(
            "(status = 'ok') = (basic_price IS NOT NULL AND product_price IS NOT NULL)",
            name=op.f('ck_wish_price_observation_prices_iff_ok'),
        ),
        sa.ForeignKeyConstraint(
            ['wish_id'],
            ['wish.id'],
            name=op.f('fk_wish_price_observation_wish_id_wish'),
            ondelete='CASCADE',
        ),
        # Составной ключ = «одна хотелка, одни сутки»: держит ON CONFLICT DO NOTHING
        # при повторном обходе и не даёт таблице расти быстрее одной строки в день.
        sa.PrimaryKeyConstraint(
            'wish_id', 'observed_date', name=op.f('pk_wish_price_observation')
        ),
    )


def downgrade() -> None:
    op.drop_table('wish_price_observation')
    price_observation_status.drop(op.get_bind())
    shop.drop(op.get_bind())
