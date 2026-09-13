"""follow_event.is_notification_sent — пуш о подписчиках уходит кроном

Пуш «новый подписчик» больше не ставится на каждое событие подписки, а
собирается ежечасным кроном в одно сообщение. Флаг отмечает события, по
которым пуш уже ушёл. Существующие события помечаем отправленными, иначе
первый прогон крона разошлёт пуши за всю историю подписок.

Revision ID: d8f5b2e0a3c4
Revises: c7e4a1d9f2b3
Create Date: 2026-09-13 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd8f5b2e0a3c4'
down_revision: str | None = 'c7e4a1d9f2b3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'follow_event',
        sa.Column(
            'is_notification_sent',
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    # Дальше новые события — неотправленные; дефолт true был нужен только бэкфиллу.
    op.alter_column('follow_event', 'is_notification_sent', server_default=sa.false())


def downgrade() -> None:
    op.drop_column('follow_event', 'is_notification_sent')
