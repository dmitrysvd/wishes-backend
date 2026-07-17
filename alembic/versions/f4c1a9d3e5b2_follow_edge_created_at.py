"""user_following.created_at: время создания подписки

Инструментация follow-графа: с этой колонкой видно, когда образуются рёбра
(рост графа во времени, корреляция с релизами/сезоном). Колонка nullable —
у существующих рёбер реальная дата неизвестна, поэтому они остаются NULL
(легаси, «до инструментации»); дефолт проставляется отдельным ALTER, чтобы
ADD COLUMN не забэкфиллил старые строки временем миграции. Новые подписки
получают now() на уровне БД.

Revision ID: f4c1a9d3e5b2
Revises: e7a3c9f1b2d4
Create Date: 2026-07-17 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f4c1a9d3e5b2'
down_revision: str | None = 'e7a3c9f1b2d4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Добавляем колонку БЕЗ server_default — иначе Postgres забэкфиллит все
    # существующие рёбра временем миграции (ложная дата создания).
    op.add_column(
        'user_following',
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    )
    # Дефолт ставим отдельно: действует только на будущие вставки.
    op.execute('ALTER TABLE user_following ALTER COLUMN created_at SET DEFAULT now()')


def downgrade() -> None:
    op.drop_column('user_following', 'created_at')
