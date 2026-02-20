"""Postgres initial

Revision ID: 71c61be4c32e
Revises:
Create Date: 2026-02-20 10:09:43.753683

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '71c61be4c32e'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('display_name', sa.String(length=250), nullable=False),
        sa.Column('email', sa.String(length=100), nullable=True),
        sa.Column('phone', sa.String(length=15), nullable=True),
        sa.Column('birth_date', sa.Date(), nullable=True),
        sa.Column('gender', postgresql.ENUM('male', 'female', name='gender', create_type=False), nullable=True),
        sa.Column('photo_url', sa.String(length=1024), nullable=True),
        sa.Column('photo_path', sa.String(length=200), nullable=True),
        sa.Column('vk_id', sa.String(length=15), nullable=True),
        sa.Column('vk_access_token', sa.String(length=500), nullable=True),
        sa.Column('vk_friends_data', sa.JSON(), nullable=True),
        sa.Column('firebase_uid', sa.String(length=1000), nullable=False),
        sa.Column('firebase_push_token', sa.String(length=1000), nullable=True),
        sa.Column('firebase_push_token_saved_at', sa.DateTime(), nullable=True),
        sa.Column('registered_at', sa.DateTime(), nullable=False),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
        sa.Column(
            'pre_bday_push_for_followers_last_sent_at', sa.DateTime(), nullable=True
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
        sa.UniqueConstraint('firebase_uid'),
        sa.UniqueConstraint('vk_access_token'),
        sa.UniqueConstraint('vk_id'),
    )
    op.create_table(
        'push_sending_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sent_at', sa.DateTime(), nullable=False),
        sa.Column('reason_user_id', sa.Uuid(), nullable=False),
        sa.Column('target_user_id', sa.Uuid(), nullable=False),
        sa.Column(
            'reason',
            postgresql.ENUM('CURRENT_USER_BIRTHDAY', 'FOLLOWER_BIRTHDAY', name='pushreason', create_type=False),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['reason_user_id'], ['user.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'user_following',
        sa.Column('follower_id', sa.Uuid(), nullable=False),
        sa.Column('followed_id', sa.Uuid(), nullable=False),
        sa.CheckConstraint('follower_id <> followed_id'),
        sa.ForeignKeyConstraint(['followed_id'], ['user.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['follower_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('follower_id', 'followed_id'),
    )
    op.create_table(
        'wish',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('reserved_by_id', sa.Uuid(), nullable=True),
        sa.Column('name', sa.String(length=250), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('link', sa.String(length=500), nullable=True),
        sa.Column('price', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('image', sa.String(length=500), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('is_archived', sa.Boolean(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('is_reservation_notification_sent', sa.Boolean(), nullable=False),
        sa.Column('is_creation_notification_sent', sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            'user_id <> reserved_by_id', name='wish_user_not_equal_reserved_by'
        ),
        sa.ForeignKeyConstraint(
            ['reserved_by_id'],
            ['user.id'],
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['user.id'],
        ),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('wish')
    op.drop_table('user_following')
    op.drop_table('push_sending_log')
    op.drop_table('user')
