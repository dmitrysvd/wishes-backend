"""seasonal push reason + campaign_key

Сезонные глобальные пуши: добавляем значение enum `SEASONAL` в тип `pushreason`
и nullable-колонку `campaign_key` в `push_sending_log` для дедупа кампаний.

Revision ID: b8e3f1a2c4d7
Revises: a7d2e4f8c1b6
Create Date: 2026-07-18 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b8e3f1a2c4d7'
down_revision: str | None = 'a7d2e4f8c1b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Добавление значения в enum Postgres нельзя выполнять внутри транзакции —
    # используем autocommit_block. IF NOT EXISTS делает шаг идемпотентным.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE pushreason ADD VALUE IF NOT EXISTS 'SEASONAL'")
    op.add_column(
        'push_sending_log',
        sa.Column('campaign_key', sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('push_sending_log', 'campaign_key')
    # Значение 'SEASONAL' из enum-типа pushreason штатно удалить нельзя
    # (Postgres не поддерживает DROP VALUE) — оставляем его в типе, оно
    # безвредно и не используется после отката.
