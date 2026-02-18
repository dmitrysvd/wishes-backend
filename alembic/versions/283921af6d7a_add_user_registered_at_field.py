"""Add User.registered_at field

Revision ID: 283921af6d7a
Revises: c7a4c2ed6580
Create Date: 2024-02-08 20:56:17.493594

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '283921af6d7a'
down_revision: Union[str, None] = 'c7a4c2ed6580'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('PRAGMA foreign_keys=OFF;')
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'registered_at',
                sa.DateTime(),
                nullable=False,
                server_default=sa.text('(CURRENT_TIMESTAMP)'),
            )
        )
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.alter_column('registered_at', server_default=None)


def downgrade() -> None:
    op.execute('PRAGMA foreign_keys=OFF;')
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('registered_at')
