"""Add is_notification_sent fields

Revision ID: 9fdce23cf0a4
Revises: e22f857de4f6
Create Date: 2024-02-04 18:56:56.867682

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9fdce23cf0a4'
down_revision: Union[str, None] = 'e22f857de4f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('wish', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'is_reservation_notification_sent',
                sa.Boolean(),
                nullable=False,
                server_default='1',
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('wish', schema=None) as batch_op:
        batch_op.drop_column('is_reservation_notification_sent')
