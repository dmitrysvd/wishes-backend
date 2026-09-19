"""Перенос данных в миграции a7c3e5f1b9d2: токены юзеров → установки."""

from datetime import datetime, timedelta, timezone

from alembic.config import Config
from sqlalchemy import text

from alembic import command
from app.config import settings

REV = 'a7c3e5f1b9d2'


def _cfg() -> Config:
    cfg = Config()
    cfg.set_main_option('script_location', 'alembic')
    cfg.set_main_option('sqlalchemy.url', settings.TEST_DATABASE_URL)
    return cfg


def test_tokens_become_installations_latest_owner_wins(test_engine):
    cfg = _cfg()
    command.downgrade(cfg, f'{REV}-1')
    now = datetime(2026, 9, 19, tzinfo=timezone.utc)
    with test_engine.begin() as conn:
        conn.execute(text('DELETE FROM "user"'))
        # A и B делят один токен (один телефон, два аккаунта): B сохранил позже.
        # C — без токена; D — с токеном без даты.
        conn.execute(
            text(
                'INSERT INTO "user" (id, display_name, firebase_uid, '
                'firebase_push_token, firebase_push_token_saved_at, registered_at) '
                'VALUES '
                "(gen_random_uuid(), 'A', 'a', 'shared', :older, :now), "
                "(gen_random_uuid(), 'B', 'b', 'shared', :now, :now), "
                "(gen_random_uuid(), 'C', 'c', NULL, NULL, :now), "
                "(gen_random_uuid(), 'D', 'd', 'own', NULL, :now)"
            ),
            {'older': now - timedelta(days=1), 'now': now},
        )
    try:
        command.upgrade(cfg, 'head')
        with test_engine.connect() as conn:
            rows = conn.execute(
                text(
                    'SELECT u.display_name, p.fid, p.push_token '
                    'FROM push_installation p JOIN "user" u ON u.id = p.user_id '
                    'ORDER BY p.push_token'
                )
            ).all()
        assert rows == [('D', None, 'own'), ('B', None, 'shared')]
    finally:
        with test_engine.begin() as conn:
            conn.execute(text('DELETE FROM "user"'))
