"""убрать wish.is_reservation_notification_sent

Пуш «резерв» теперь решается по `wish.reserved_at` и последнему такому пушу в
`push_sending_log`; флаг больше никто не читает.

Revision ID: c7e2a9f4b1d3
Revises: 3abeedd06809
Create Date: 2026-10-01 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c7e2a9f4b1d3'
down_revision: str | None = '3abeedd06809'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column('wish', 'is_reservation_notification_sent')


def downgrade() -> None:
    # true: после отката старый крон не разошлёт пуши обо всех прошлых бронях.
    op.add_column(
        'wish',
        sa.Column(
            'is_reservation_notification_sent',
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )
