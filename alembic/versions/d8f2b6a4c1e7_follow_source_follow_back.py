"""вход в граф (0024): значения enum followsource и pushreason

`followsource`: подписка с профиля, открытого из пуша, «В ответ» из своего списка
подписчиков и серверное `invite` — взаимные подписки по инвайт-ссылке.
`pushreason`: пуш пригласившему о регистрации по его ссылке. Схему таблиц не
меняем.

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
        op.execute("ALTER TYPE followsource ADD VALUE IF NOT EXISTS 'invite'")
        op.execute("ALTER TYPE pushreason ADD VALUE IF NOT EXISTS 'INVITE_JOINED'")


def downgrade() -> None:
    # No-op: Postgres не умеет DROP VALUE у enum-типа. Оставшиеся значения
    # безвредны — на откате их просто игнорируем.
    pass
