"""empty-list reactivation: значение enum pushreason

Реактивационный пуш пользователям с пустым списком желаний. Добавляем новое
значение в enum `pushreason` — схему таблиц не меняем.

Revision ID: b9e3f1a7c4d2
Revises: c1d2e3f4a5b6
Create Date: 2026-07-18 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b9e3f1a7c4d2'
down_revision: str | None = 'c1d2e3f4a5b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ADD VALUE у enum в Postgres нельзя выполнять внутри транзакции —
    # оборачиваем в autocommit_block.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE pushreason ADD VALUE IF NOT EXISTS 'EMPTY_LIST_REACTIVATION'"
        )


def downgrade() -> None:
    # No-op: Postgres не умеет DROP VALUE у enum-типа. Оставшееся значение
    # безвредно — на откате его просто игнорируем.
    pass
