"""follow_event: append-only лог событий подписки с источником

Инструментация follow-графа №2: логируем follow/unfollow во времени с меткой
источника (`source`) — с какого экрана пришли на профиль перед действием.
В отличие от таблицы рёбер `user_following`, лог копит и отписки (сигнал оттока
связей). `source` nullable — старые клиенты метку не шлют.

Revision ID: a7d2e4f8c1b6
Revises: f4c1a9d3e5b2
Create Date: 2026-07-17 22:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7d2e4f8c1b6'
down_revision: str | None = 'f4c1a9d3e5b2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

follow_action = sa.Enum('follow', 'unfollow', name='followaction')
follow_source = sa.Enum(
    'search',
    'possible_friends',
    'followers_list',
    'deeplink',
    'other',
    name='followsource',
)


def upgrade() -> None:
    op.create_table(
        'follow_event',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('actor_id', sa.Uuid(), nullable=False),
        sa.Column('target_id', sa.Uuid(), nullable=False),
        sa.Column('action', follow_action, nullable=False),
        sa.Column('source', follow_source, nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['actor_id'], ['user.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('follow_event')
    follow_source.drop(op.get_bind())
    follow_action.drop(op.get_bind())
