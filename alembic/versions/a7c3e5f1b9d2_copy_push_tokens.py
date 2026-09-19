"""перенос user.firebase_push_token в push_installation (фича 0016)

Текущий токен юзера становится его первой установкой, без FID: FID приходит
только от клиента — префикс токена FCM как FID не принимает (проверено dry-run
на проде 2026-09-19). Мёртвые токены не отсеиваем — их удалит `send_push` по
«unregistered» при первом пуше. Старые колонки `user` остаются нетронутыми
(снимок для отката кода). Идемпотентно: токен, уже лежащий в установках,
не дублируется.

Revision ID: a7c3e5f1b9d2
Revises: f0a1b2c3d4e5
Create Date: 2026-09-19 14:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7c3e5f1b9d2'
down_revision: str | None = 'f0a1b2c3d4e5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO push_installation (id, user_id, fid, push_token, saved_at)
        SELECT DISTINCT ON (u.firebase_push_token)
               gen_random_uuid(), u.id, NULL, u.firebase_push_token,
               COALESCE(u.firebase_push_token_saved_at, now())
        FROM "user" u
        WHERE u.firebase_push_token IS NOT NULL
          AND u.firebase_push_token <> ''
          AND NOT EXISTS (
              SELECT 1 FROM push_installation p
              WHERE p.push_token = u.firebase_push_token
          )
        -- Один токен мог осесть у двух аккаунтов (A вышел, B вошёл на том же
        -- телефоне; на проде таких 25): установка одна — тому, кто сохранил
        -- её последним.
        ORDER BY u.firebase_push_token, u.firebase_push_token_saved_at DESC NULLS LAST
        """
    )


def downgrade() -> None:
    # Перенесённые строки не отличить от пришедших через ручку — не трогаем.
    pass
