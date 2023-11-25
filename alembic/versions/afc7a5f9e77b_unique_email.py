"""Unique email

Revision ID: afc7a5f9e77b
Revises: 582b23eb3019
Create Date: 2023-11-25 08:38:24.460975

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'afc7a5f9e77b'
down_revision: Union[str, None] = '582b23eb3019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT_NAME = 'unique_email'


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('user') as batch_op:
        batch_op.create_unique_constraint(CONSTRAINT_NAME, ['email'])
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('user') as batch_op:
        batch_op.drop_constraint(CONSTRAINT_NAME, type_='unique')
    # ### end Alembic commands ###