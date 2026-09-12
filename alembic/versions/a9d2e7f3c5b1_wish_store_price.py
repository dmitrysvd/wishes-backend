"""wish: источник цены и последнее наблюдение магазина; лог нажатий (фича 0011)

Цена у хотелки со ссылкой на поддерживаемый магазин становится живой: нужен
источник (`shop`/`manual`), признак «от» и денормализованное последнее
наблюдение, чтобы списки не ходили в историю за каждой строкой. Плюс
append-only лог нажатий «актуальная с WB» — счётчик критерия приёмки.

Данные: все существующие хотелки с WB-ссылкой переводятся в `shop` — цифра в
них была снимком на момент добавления, а не осознанной правкой (автозаполнения
не было); цену и наблюдение подтянет ближайший суточный обход. Остальные —
`manual`.

Revision ID: a9d2e7f3c5b1
Revises: c7e2a9d4f1b3
Create Date: 2026-09-12 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a9d2e7f3c5b1'
down_revision: str | None = 'c7e2a9d4f1b3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

price_source = sa.Enum('shop', 'manual', name='pricesource')
price_refresh_outcome = sa.Enum(
    'ok', 'unsupported', 'failed', name='pricerefreshoutcome'
)
# Тип уже создан миграцией c7e2a9d4f1b3 — переиспользуем, не создаём.
price_observation_status = postgresql.ENUM(
    'ok', 'sold_out', 'gone', name='priceobservationstatus', create_type=False
)

# То же правило «что такое WB-ссылка», что в `app.parsers.parse_wildberries_link`:
# домен wildberries.ru и `catalog/<число>` в пути.
WB_LINK_SQL = r"link LIKE '%wildberries.ru%' AND link ~ 'catalog/\d+'"


def upgrade() -> None:
    # add_column тип сам не создаёт (create_table ниже — создаёт).
    price_source.create(op.get_bind())
    op.add_column(
        'wish',
        sa.Column(
            'price_source',
            price_source,
            nullable=False,
            server_default='manual',
        ),
    )
    op.add_column(
        'wish',
        sa.Column(
            'price_is_minimum',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        'wish',
        sa.Column('store_availability', price_observation_status, nullable=True),
    )
    op.add_column(
        'wish',
        sa.Column('store_observed_at', sa.DateTime(timezone=True), nullable=True),
    )
    # Существующие WB-хотелки → магазинная цена (решение продукта, intent 0011).
    op.execute(f"UPDATE wish SET price_source = 'shop' WHERE {WB_LINK_SQL}")
    # server_default нужен только на момент заполнения: дальше значения ставит ORM.
    op.alter_column('wish', 'price_source', server_default=None)
    op.alter_column('wish', 'price_is_minimum', server_default=None)

    op.create_table(
        'wish_price_refresh_event',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('wish_id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('outcome', price_refresh_outcome, nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['wish_id'],
            ['wish.id'],
            name=op.f('fk_wish_price_refresh_event_wish_id_wish'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['user.id'],
            name=op.f('fk_wish_price_refresh_event_user_id_user'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_wish_price_refresh_event')),
    )


def downgrade() -> None:
    op.drop_table('wish_price_refresh_event')
    op.drop_column('wish', 'store_observed_at')
    op.drop_column('wish', 'store_availability')
    op.drop_column('wish', 'price_is_minimum')
    op.drop_column('wish', 'price_source')
    price_refresh_outcome.drop(op.get_bind())
    price_source.drop(op.get_bind())
