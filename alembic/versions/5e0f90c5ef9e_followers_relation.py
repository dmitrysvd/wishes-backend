"""Followers relation

Revision ID: 5e0f90c5ef9e
Revises: f2485a629978
Create Date: 2023-11-26 16:35:08.930691

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '5e0f90c5ef9e'
down_revision: Union[str, None] = 'f2485a629978'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        'user_following',
        sa.Column('follower_id', sa.Integer(), nullable=False),
        sa.Column('followed_id', sa.Integer(), nullable=False),
        sa.CheckConstraint('follower_id <> followed_id'),
        sa.ForeignKeyConstraint(['followed_id'], ['user.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['follower_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('follower_id', 'followed_id'),
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('user_following')
    # ### end Alembic commands ###
