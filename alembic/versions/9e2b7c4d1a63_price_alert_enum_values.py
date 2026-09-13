"""pushreason.PRICE_ALERT и notificationgroup.prices (фича 0013)

Группа «Цены и наличие» появляется на экране настроек с этой миграцией; вид
пуша `PRICE_ALERT` — для лога отправок. Схему таблиц не меняем.

Revision ID: 9e2b7c4d1a63
Revises: 78da60b33650
Create Date: 2026-09-13 20:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9e2b7c4d1a63'
down_revision: str | None = '78da60b33650'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ADD VALUE у enum в Postgres нельзя выполнять внутри транзакции —
    # оборачиваем в autocommit_block.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE pushreason ADD VALUE IF NOT EXISTS 'PRICE_ALERT'")
        op.execute(
            "ALTER TYPE notificationgroup ADD VALUE IF NOT EXISTS 'prices' "
            "BEFORE 'tips'"
        )


def downgrade() -> None:
    # No-op: Postgres не умеет DROP VALUE у enum-типа. Оставшиеся значения
    # безвредны — на откате их просто игнорируем.
    pass
