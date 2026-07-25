"""user_activity_day + wish.reserved_at: приборы возврата

`user.last_login_at` хранит только последний вход и затирается следующим — по
нему нельзя ни посчитать честный DAU/WAU/MAU, ни увидеть, вернулся ли человек к
следующему поводу. Заводим суточный след: одна строка на юзера в сутки (upsert),
плюс счётчик открытий бёрздей-радара, чтобы отделить работу фичи 0007 от фона.

`wish.reserved_at` — момент резерва: без него резерв нельзя отнести ко времени и
проверить, даёт ли повод прирост подарков. Nullable: NULL — либо не
зарезервировано, либо легаси-резерв, сделанный до инструментации.

Revision ID: d1a4c7b93e08
Revises: a2f7c1d9e4b8
Create Date: 2026-07-25 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd1a4c7b93e08'
down_revision: str | None = 'a2f7c1d9e4b8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'user_activity_day',
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('activity_date', sa.Date(), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('request_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('radar_open_count', sa.Integer(), server_default='0', nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        # Составной ключ = дедуп «один юзер, одни сутки» на уровне БД: именно он
        # держит upsert и ограничивает рост таблицы.
        sa.PrimaryKeyConstraint('user_id', 'activity_date'),
    )
    # Срезы «кто был активен в период» идут по дате — отдельный индекс под них.
    op.create_index(
        'ix_user_activity_day_activity_date', 'user_activity_day', ['activity_date']
    )
    op.add_column(
        'wish', sa.Column('reserved_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('wish', 'reserved_at')
    op.drop_index('ix_user_activity_day_activity_date', table_name='user_activity_day')
    op.drop_table('user_activity_day')
