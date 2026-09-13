# Wishes (Хотелки) — бэкенд

[![CI](https://github.com/dmitrysvd/wishes-backend/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/dmitrysvd/wishes-backend/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/dmitrysvd/wishes-backend/python-coverage-comment-action-data/endpoint.json)](https://github.com/dmitrysvd/wishes-backend/tree/python-coverage-comment-action-data)

Стек, структура и правила — в [AGENTS.md](AGENTS.md).

## Локальный запуск для e2e фронта

```bash
scripts/dev-server.sh [порт]   # миграции + uvicorn --reload на 0.0.0.0:8000
```

Swagger — `/docs`, спек — `/openapi.json`. Бэк-сессия Claude живёт в сетевом
namespace: с хоста бэк доступен по IP veth (`10.200.1.2:<порт>`), не по `127.0.0.1`.
Авторизация в тестах без OAuth — `POST /dev/test_token` с `TEST_AUTH_SECRET` из `.env`
(фича 0009, персоны `rich` / `empty`).
