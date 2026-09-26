"""follow source: значения push и followers_follow_back

Фича 0024: подписка с профиля, открытого из пуша, и «В ответ» из своего списка
подписчиков. Добавляем значения в enum `followsource` — схему таблиц не меняем.

Revision ID: d8f2b6a4c1e7
Revises: a7c3e5f1b9d2
Create Date: 2026-09-26 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd8f2b6a4c1e7'
down_revision: str | None = 'a7c3e5f1b9d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ADD VALUE у enum в Postgres нельзя выполнять внутри транзакции —
    # оборачиваем в autocommit_block.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE followsource ADD VALUE IF NOT EXISTS 'push'")
        op.execute(
            "ALTER TYPE followsource ADD VALUE IF NOT EXISTS 'followers_follow_back'"
        )


def downgrade() -> None:
    # No-op: Postgres не умеет DROP VALUE у enum-типа. Оставшиеся значения
    # безвредны — на откате их просто игнорируем.
    pass
