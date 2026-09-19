"""push_installation — адреса пушей на установку (фича 0016)

Таблица установок юзера: FID + FCM-токен на каждую, юзер может иметь несколько.
Данные из `user.firebase_push_token` сюда НЕ переносятся миграцией: перенос
делает `scripts/migrate_push_installations.py` — ему нужен dry-run в FCM на
каждый адрес. Старые колонки `user` остаются (снимок для отката кода).

Revision ID: f0a1b2c3d4e5
Revises: e1a7c3b9d2f4
Create Date: 2026-09-19 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f0a1b2c3d4e5'
down_revision: str | None = 'e1a7c3b9d2f4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'push_installation',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('fid', sa.String(length=100), nullable=True),
        sa.Column('push_token', sa.String(length=1000), nullable=False),
        sa.Column('saved_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "fid <> ''", name=op.f('ck_push_installation_fid_not_empty')
        ),
        sa.CheckConstraint(
            "push_token <> ''", name=op.f('ck_push_installation_push_token_not_empty')
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['user.id'],
            name=op.f('fk_push_installation_user_id_user'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_push_installation')),
        sa.UniqueConstraint('fid', name=op.f('uq_push_installation_fid')),
        sa.UniqueConstraint('push_token', name=op.f('uq_push_installation_push_token')),
    )
    op.create_index(
        op.f('ix_push_installation_user_id'), 'push_installation', ['user_id']
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_push_installation_user_id'), table_name='push_installation')
    op.drop_table('push_installation')
