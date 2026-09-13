"""Пуши по складу (фича 0013): база «видел» у хотелки, триггер и открытие в логе

`wish.alert_base_price/alert_base_at` — цена, от которой считается порог
«подешевело» (NULL — берётся наблюдение на последний день активности).
`push_sending_log.trigger/opened_at` — тип триггера и момент открытия по пушу
(`POST /push/opened`). Бэкфилла нет: у существующих строк NULL.

Revision ID: adef22e9beb9
Revises: 9e2b7c4d1a63
Create Date: 2026-09-13 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'adef22e9beb9'
down_revision: str | None = '9e2b7c4d1a63'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

price_alert_trigger = sa.Enum(
    'price', 'availability', 'mixed', name='pricealerttrigger'
)


def upgrade() -> None:
    # add_column тип сам не создаёт.
    price_alert_trigger.create(op.get_bind())
    op.add_column(
        'push_sending_log',
        sa.Column('trigger', price_alert_trigger, nullable=True),
    )
    op.add_column(
        'push_sending_log',
        sa.Column('opened_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'wish',
        sa.Column('alert_base_price', sa.Numeric(precision=12, scale=2), nullable=True),
    )
    op.add_column(
        'wish',
        sa.Column('alert_base_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('wish', 'alert_base_at')
    op.drop_column('wish', 'alert_base_price')
    op.drop_column('push_sending_log', 'opened_at')
    op.drop_column('push_sending_log', 'trigger')
    price_alert_trigger.drop(op.get_bind())
