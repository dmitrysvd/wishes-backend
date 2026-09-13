#!/usr/bin/env bash
# Локальный бэк с текущей ветки для e2e фронта без деплоя: накатить миграции
# и поднять uvicorn с автоперезагрузкой. Слушает 0.0.0.0, чтобы из-за границы
# сетевого namespace (фронт на хосте) бэк был доступен по IP veth, а не только
# по 127.0.0.1. БД и секреты — из .env (в worktree это симлинк на основной).
#
#   scripts/dev-server.sh [порт]      # по умолчанию 8000
#
# Сид-юзер для авторизации без OAuth: POST /dev/test_token
# {"secret": <TEST_AUTH_SECRET из .env>, "persona": "rich" | "empty"}.
set -euo pipefail

PORT="${1:-8000}"
cd "$(dirname "${BASH_SOURCE[0]}")/.."

uv run alembic upgrade head
exec uv run uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --reload
