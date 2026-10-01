"""колонки времени без пояса → timestamptz

Старые значения записаны в UTC (процессы и БД в проде живут в Etc/UTC,
проверено 2026-10-01), поэтому `USING ... AT TIME ZONE 'UTC'`: без явного пояса
Postgres перевёл бы их через TimeZone сессии миграции и сдвинул бы историю.

Revision ID: b3d9f2a6c8e1
Revises: a7c3e5f1b9d2
Create Date: 2026-10-01 16:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b3d9f2a6c8e1'
down_revision: str | None = 'a7c3e5f1b9d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    ('user', 'firebase_push_token_saved_at'),
    ('user', 'registered_at'),
    ('user', 'last_login_at'),
    ('user', 'pre_bday_push_for_followers_last_sent_at'),
    ('push_sending_log', 'sent_at'),
)


def upgrade() -> None:
    for table, column in COLUMNS:
        op.execute(
            f'ALTER TABLE "{table}" ALTER COLUMN {column} TYPE timestamptz '
            f"USING {column} AT TIME ZONE 'UTC'"
        )


def downgrade() -> None:
    for table, column in COLUMNS:
        op.execute(
            f'ALTER TABLE "{table}" ALTER COLUMN {column} TYPE timestamp '
            f"USING {column} AT TIME ZONE 'UTC'"
        )
