"""is_test: пометка сид-юзера dev/test-байпаса

Флаг отмечает детерминированных сид-аккаунтов dev/test-байпаса аутентификации
(фича 0009). Токен по секрету выдаётся и принимается ТОЛЬКО для таких юзеров —
даже утёкший секрет не даёт войти в реальный аккаунт. Дефолт `false`: все
существующие (реальные) пользователи — не тестовые.

Revision ID: a2f7c1d9e4b8
Revises: b9e3f1a7c4d2
Create Date: 2026-07-23 21:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a2f7c1d9e4b8'
down_revision: str | None = 'b9e3f1a7c4d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'user',
        sa.Column(
            'is_test',
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('user', 'is_test')
