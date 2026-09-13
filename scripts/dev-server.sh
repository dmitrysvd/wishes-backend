#!/usr/bin/env bash
# Локальный бэк с текущей ветки для e2e фронта без деплоя: накатить миграции
# и поднять uvicorn с автоперезагрузкой. БД и секреты — из .env (в worktree это
# симлинк на основной). Ручки отдаются без префикса /api/v1 — его добавляет
# nginx на проде.
#
#   scripts/dev-server.sh [порт]      # по умолчанию 8000
#   DEV_SERVER_HOST=0.0.0.0 scripts/dev-server.sh   # если клиент на другом хосте
#
# Сид-юзер для авторизации без OAuth: POST /dev/test_token
# {"secret": <TEST_AUTH_SECRET из .env>, "persona": "rich" | "empty"}.
set -euo pipefail

PORT="${1:-8000}"
HOST="${DEV_SERVER_HOST:-127.0.0.1}"
cd "$(dirname "${BASH_SOURCE[0]}")/.."

uv run alembic upgrade head
exec uv run uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
