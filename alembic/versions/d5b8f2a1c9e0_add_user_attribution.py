"""user_attribution: реферальная атрибуция регистрации

Новая таблица под first-touch атрибуцию (фича 0003): кто привёл нового юзера
(`referrer_id`) и через какой канал он установил приложение (`utm_source`).
Связь 1:1 к `user` (unique `user_id`). Вынесена отдельно, чтобы поверх неё
наращивать будущие фичи роста без изменения таблицы `user`.

Revision ID: d5b8f2a1c9e0
Revises: c3e9a1b7d2f4
Create Date: 2026-07-02 19:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd5b8f2a1c9e0'
down_revision: str | None = 'c3e9a1b7d2f4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'user_attribution',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('referrer_id', sa.Uuid(), nullable=True),
        sa.Column('utm_source', sa.String(length=64), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            'user_id <> referrer_id', name='attribution_not_self_referral'
        ),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['referrer_id'], ['user.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id'),
    )


def downgrade() -> None:
    op.drop_table('user_attribution')
