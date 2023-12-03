"""Wish link

Revision ID: 3a27af32987e
Revises: d7db9b0db77c
Create Date: 2023-12-03 15:09:27.013724

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3a27af32987e'
down_revision: Union[str, None] = 'd7db9b0db77c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('wish', sa.Column('link', sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column('wish', 'link')
