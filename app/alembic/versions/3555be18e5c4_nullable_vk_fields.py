"""Nullable vk fields

Revision ID: 3555be18e5c4
Revises: 1382cce781a5
Create Date: 2023-12-16 21:07:53.340825

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3555be18e5c4'
down_revision: Union[str, None] = '1382cce781a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('user') as batch_op:
        batch_op.alter_column(
            'gender', existing_type=sa.VARCHAR(length=6), nullable=True
        )
        batch_op.alter_column(
            'photo_url', existing_type=sa.VARCHAR(length=200), nullable=True
        )
        batch_op.alter_column(
            'vk_id', existing_type=sa.VARCHAR(length=15), nullable=True
        )
        batch_op.alter_column(
            'vk_friends_data', existing_type=sqlite.JSON(), nullable=True
        )


def downgrade() -> None:
    with op.batch_alter_table('user') as batch_op:
        batch_op.alter_column(
            'vk_friends_data', existing_type=sqlite.JSON(), nullable=False
        )
        batch_op.alter_column(
            'vk_id', existing_type=sa.VARCHAR(length=15), nullable=False
        )
        batch_op.alter_column(
            'photo_url', existing_type=sa.VARCHAR(length=200), nullable=False
        )
        batch_op.alter_column(
            'gender', existing_type=sa.VARCHAR(length=6), nullable=False
        )
