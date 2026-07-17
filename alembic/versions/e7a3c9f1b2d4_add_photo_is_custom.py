"""photo_is_custom: пометка кастомной аватарки

Флаг отличает фото, загруженное пользователем вручную (`set_profile_image`),
от аватарки, взятой из соц-сети. Нужен, чтобы будущий refresh-на-логине и
бэкфилл на диск не затирали кастомное фото соц-сетевым. Дефолт `false`:
существующие соц-аватарки (и мигрируемые на диск) считаются некастомными.

Revision ID: e7a3c9f1b2d4
Revises: d5b8f2a1c9e0
Create Date: 2026-07-17 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e7a3c9f1b2d4'
down_revision: str | None = 'd5b8f2a1c9e0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'user',
        sa.Column(
            'photo_is_custom',
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('user', 'photo_is_custom')
