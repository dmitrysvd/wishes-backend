"""Add creation notification field fields

Revision ID: c7a4c2ed6580
Revises: 9fdce23cf0a4
Create Date: 2024-02-05 18:36:02.087141

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c7a4c2ed6580'
down_revision: Union[str, None] = '9fdce23cf0a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('wish', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'created_at',
                sa.DateTime(timezone=True),
                server_default=sa.text('(CURRENT_TIMESTAMP)'),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                'is_creation_notification_sent',
                sa.Boolean(),
                nullable=False,
                server_default='1',
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('wish', schema=None) as batch_op:
        batch_op.drop_column('is_creation_notification_sent')
        batch_op.drop_column('created_at')
