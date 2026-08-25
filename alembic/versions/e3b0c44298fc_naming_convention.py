"""Привести имена констрейнтов к naming_convention

Без конвенции имена безымянным ограничениям раздаёт Postgres, а alembic сличает
их по имени — autogenerate и `alembic check` на них ненадёжны. Конвенция задана
в `Base.metadata`; здесь одноразово переименовываем то, что уже создано.

Только переименования: данные и структура не меняются, DDL транзакционный.

Revision ID: e3b0c44298fc
Revises: d1a4c7b93e08
Create Date: 2026-08-26 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'e3b0c44298fc'
down_revision: str | None = 'd1a4c7b93e08'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (таблица, старое имя, новое имя). Индексы, обслуживающие PK/UNIQUE, Postgres
# переименовывает вместе с констрейнтом — отдельных ALTER INDEX не нужно.
RENAMES: list[tuple[str, str, str]] = [
    ('user', 'user_pkey', 'pk_user'),
    ('user', 'user_email_key', 'uq_user_email'),
    ('user', 'user_firebase_uid_key', 'uq_user_firebase_uid'),
    ('user', 'user_vk_access_token_key', 'uq_user_vk_access_token'),
    ('user', 'user_vk_id_key', 'uq_user_vk_id'),
    ('user', 'push_token_not_empty', 'ck_user_push_token_not_empty'),
    ('wish_recommendation', 'wish_recommendation_pkey', 'pk_wish_recommendation'),
    ('follow_event', 'follow_event_pkey', 'pk_follow_event'),
    ('follow_event', 'follow_event_actor_id_fkey', 'fk_follow_event_actor_id_user'),
    ('follow_event', 'follow_event_target_id_fkey', 'fk_follow_event_target_id_user'),
    ('push_sending_log', 'push_sending_log_pkey', 'pk_push_sending_log'),
    (
        'push_sending_log',
        'push_sending_log_reason_user_id_fkey',
        'fk_push_sending_log_reason_user_id_user',
    ),
    (
        'push_sending_log',
        'push_sending_log_target_user_id_fkey',
        'fk_push_sending_log_target_user_id_user',
    ),
    ('user_activity_day', 'user_activity_day_pkey', 'pk_user_activity_day'),
    (
        'user_activity_day',
        'user_activity_day_user_id_fkey',
        'fk_user_activity_day_user_id_user',
    ),
    ('user_attribution', 'user_attribution_pkey', 'pk_user_attribution'),
    (
        'user_attribution',
        'user_attribution_referrer_id_fkey',
        'fk_user_attribution_referrer_id_user',
    ),
    (
        'user_attribution',
        'user_attribution_user_id_fkey',
        'fk_user_attribution_user_id_user',
    ),
    ('user_attribution', 'user_attribution_user_id_key', 'uq_user_attribution_user_id'),
    (
        'user_attribution',
        'attribution_not_self_referral',
        'ck_user_attribution_not_self_referral',
    ),
    ('user_following', 'user_following_pkey', 'pk_user_following'),
    (
        'user_following',
        'user_following_followed_id_fkey',
        'fk_user_following_followed_id_user',
    ),
    (
        'user_following',
        'user_following_follower_id_fkey',
        'fk_user_following_follower_id_user',
    ),
    ('user_following', 'user_following_check', 'ck_user_following_no_self_follow'),
    ('wish', 'wish_pkey', 'pk_wish'),
    (
        'wish',
        'fk_wish_recommendation_id',
        'fk_wish_recommendation_id_wish_recommendation',
    ),
    ('wish', 'wish_reserved_by_id_fkey', 'fk_wish_reserved_by_id_user'),
    ('wish', 'wish_user_id_fkey', 'fk_wish_user_id_user'),
    ('wish', 'wish_user_not_equal_reserved_by', 'ck_wish_user_not_equal_reserved_by'),
]


def _rename(table: str, old: str, new: str) -> None:
    """Переименовать констрейнт, если он ещё носит старое имя.

    Условие обязательно: на чистой БД `op.create_table` из ранних миграций уже
    применяет конвенцию и создаёт констрейнты сразу с новыми именами, поэтому
    переименовывать там нечего. Старые имена остались только в базах, поднятых
    до этой правки (прод, dev). Одна миграция обслуживает оба случая.
    """
    exists = (
        op.get_bind()
        .execute(
            sa.text(
                'SELECT 1 FROM pg_constraint '
                'WHERE conname = :name AND conrelid = CAST(:table AS regclass)'
            ),
            {'name': old, 'table': f'"{table}"'},
        )
        .scalar()
    )
    if exists:
        op.execute(f'ALTER TABLE "{table}" RENAME CONSTRAINT "{old}" TO "{new}"')


def upgrade() -> None:
    for table, old, new in RENAMES:
        _rename(table, old, new)


def downgrade() -> None:
    for table, old, new in reversed(RENAMES):
        _rename(table, new, old)
