"""pushreason: значения для событийных пушей

Резерв хотелки, новые хотелки подписки и новый подписчик теперь тоже пишутся в
`push_sending_log` (лог пишет сама `send_push`). Добавляем значения в enum
`pushreason` — схему таблиц не меняем.

Revision ID: c7e4a1d9f2b3
Revises: b6d3f8a2c1e7
Create Date: 2026-09-13 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c7e4a1d9f2b3'
down_revision: str | None = 'b6d3f8a2c1e7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ADD VALUE у enum в Postgres нельзя выполнять внутри транзакции —
    # оборачиваем в autocommit_block.
    with op.get_context().autocommit_block():
        for value in ('RESERVATION', 'WISH_CREATION', 'NEW_FOLLOWER'):
            op.execute(f"ALTER TYPE pushreason ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # No-op: Postgres не умеет DROP VALUE у enum-типа. Оставшиеся значения
    # безвредны — на откате их просто игнорируем.
    pass
