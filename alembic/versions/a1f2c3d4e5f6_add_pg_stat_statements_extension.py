"""add pg_stat_statements extension

Revision ID: a1f2c3d4e5f6
Revises: 7b0c3a26e25e
Create Date: 2026-06-10 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = 'a1f2c3d4e5f6'
down_revision: str | None = '7b0c3a26e25e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_stat_statements')


def downgrade() -> None:
    op.execute('DROP EXTENSION IF EXISTS pg_stat_statements')
