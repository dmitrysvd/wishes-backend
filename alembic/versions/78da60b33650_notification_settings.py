"""notification_setting: положение групп уведомлений и лог переключений (фича 0012)

Строка `notification_setting` есть только у переключавшихся групп — нет строки
= включено, поэтому бэкфилла по существующим юзерам нет. Событие пишется при
реальной смене положения (метрика «что раздражает»).

Revision ID: 78da60b33650
Revises: d8f5b2e0a3c4
Create Date: 2026-09-13 19:04:00.734125

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '78da60b33650'
down_revision: str | None = 'd8f5b2e0a3c4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Один тип на две таблицы: создаём явно один раз, в колонках — create_type=False,
# иначе второй create_table попытался бы создать тип повторно.
notification_group = postgresql.ENUM(
    'reservation', 'friends', 'birthdays', 'tips', name='notificationgroup'
)
notification_group_ref = postgresql.ENUM(
    'reservation',
    'friends',
    'birthdays',
    'tips',
    name='notificationgroup',
    create_type=False,
)


def upgrade() -> None:
    notification_group.create(op.get_bind())
    op.create_table(
        'notification_setting',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('group', notification_group_ref, nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['user.id'],
            name=op.f('fk_notification_setting_user_id_user'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_notification_setting')),
        sa.UniqueConstraint(
            'user_id', 'group', name='uq_notification_setting_user_group'
        ),
    )
    op.create_table(
        'notification_setting_event',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('group', notification_group_ref, nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['user.id'],
            name=op.f('fk_notification_setting_event_user_id_user'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_notification_setting_event')),
    )


def downgrade() -> None:
    op.drop_table('notification_setting_event')
    op.drop_table('notification_setting')
    notification_group.drop(op.get_bind())
