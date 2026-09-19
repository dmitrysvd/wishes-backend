"""push_installation — адреса пушей на установку (фича 0016)

Таблица установок юзера: FID + FCM-токен на каждую, юзер может иметь несколько.
Текущий `user.firebase_push_token` становится первой установкой юзера (без FID:
FID приходит только от клиента — префикс токена FCM как FID не принимает,
проверено dry-run на проде 2026-09-19). Мёртвые токены не отсеиваем — их
удалит `send_push` по «unregistered» при первом пуше. Старые колонки `user`
остаются нетронутыми (снимок для отката кода).

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
    op.execute(
        """
        INSERT INTO push_installation (id, user_id, fid, push_token, saved_at)
        SELECT DISTINCT ON (firebase_push_token)
               gen_random_uuid(), id, NULL, firebase_push_token,
               COALESCE(firebase_push_token_saved_at, now())
        FROM "user"
        WHERE firebase_push_token IS NOT NULL AND firebase_push_token <> ''
        -- Один токен мог осесть у двух аккаунтов (A вышел, B вошёл на том же
        -- телефоне; на проде таких 25): установка одна — тому, кто сохранил
        -- её последним.
        ORDER BY firebase_push_token, firebase_push_token_saved_at DESC NULLS LAST
        """
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_push_installation_user_id'), table_name='push_installation')
    op.drop_table('push_installation')
