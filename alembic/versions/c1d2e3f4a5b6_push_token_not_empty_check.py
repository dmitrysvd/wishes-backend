"""CHECK push_token_not_empty: запрет пустой строки в firebase_push_token

«Нет токена» кодируется как NULL; пустая строка — второе, нежелательное
представление того же состояния. Констрейнт `firebase_push_token <> ''`
запрещает '' (NULL проходит по трёхзначной логике). Прод уже чист (0 пустых),
бэкфилл не нужен.

Revision ID: c1d2e3f4a5b6
Revises: b8e3f1a2c4d7
Create Date: 2026-07-18 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: str | None = 'b8e3f1a2c4d7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        'push_token_not_empty', 'user', "firebase_push_token <> ''"
    )


def downgrade() -> None:
    op.drop_constraint('push_token_not_empty', 'user', type_='check')
