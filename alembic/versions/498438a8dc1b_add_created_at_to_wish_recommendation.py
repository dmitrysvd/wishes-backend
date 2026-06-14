"""add created_at to wish_recommendation

Revision ID: 498438a8dc1b
Revises: a1f2c3d4e5f6
Create Date: 2026-06-14 10:52:54.955418

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '498438a8dc1b'
down_revision: str | None = 'a1f2c3d4e5f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('wish_recommendation', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'created_at',
                sa.DateTime(timezone=True),
                server_default=sa.text('now()'),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('wish_recommendation', schema=None) as batch_op:
        batch_op.drop_column('created_at')
