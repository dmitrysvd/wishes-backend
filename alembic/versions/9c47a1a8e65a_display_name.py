"""Display name

Revision ID: 9c47a1a8e65a
Revises: afc7a5f9e77b
Create Date: 2023-11-25 08:47:14.685263

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9c47a1a8e65a'
down_revision: Union[str, None] = 'afc7a5f9e77b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column(
        'user', sa.Column('display_name', sa.String(length=50), nullable=False)
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('user', 'display_name')
    # ### end Alembic commands ###