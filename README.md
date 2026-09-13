# Wishes (Хотелки) — бэкенд

[![CI](https://github.com/dmitrysvd/wishes-backend/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/dmitrysvd/wishes-backend/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/dmitrysvd/wishes-backend/python-coverage-comment-action-data/endpoint.json)](https://github.com/dmitrysvd/wishes-backend/tree/python-coverage-comment-action-data)

Стек, структура и правила — в [AGENTS.md](AGENTS.md).

## Локальный запуск для e2e фронта

```bash
scripts/dev-server.sh [порт]   # миграции + uvicorn --reload на 127.0.0.1:8000
```

Адрес привязки — `DEV_SERVER_HOST` (по умолчанию `127.0.0.1`). Локальный uvicorn
отдаёт ручки **без префикса `/api/v1`** — его добавляет nginx на проде; Swagger —
`/docs`, спек — `/openapi.json`. Авторизация в тестах без OAuth —
`POST /dev/test_token` с `TEST_AUTH_SECRET` из `.env` (фича 0009, персоны
`rich` / `empty`).
