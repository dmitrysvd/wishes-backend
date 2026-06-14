# Wishes (Хотелки) — гайд для агентов

## Стек
- **Фреймворк:** FastAPI
- **БД:** PostgreSQL (и в проде, и в тестах)
- **ORM:** SQLAlchemy 2.0
- **Миграции:** Alembic
- **Зависимости:** `uv`
- **Auth:** Firebase + VK
- **Линт/типы:** `ruff` + `ty`
- **Тесты:** `pytest`
- **CI/CD**: Github CI

## pre-commit

В pre-commit вызываются: ruff format, ruff lint, ty, pytest (check cov=100%).
Если pre-commit падает на коммите — чини ошибки и повторяй, пока не пройдёт.

## Структура
- `app/main.py` — точка входа, middleware.
- `app/db.py` — модели и движок БД.
- `app/schemas.py` — Pydantic-схемы запросов/ответов.
- `app/routers/` — эндпоинты по сущностям.
- `app/dependencies.py` — общие зависимости (auth, db, теги роутеров).
- `app/helpers/` — мелкие утилиты.
- `app/cron_scripts/` — фоновые и регулярные задачи.

## Код-стайл
- Типы: `str | None`, не `Optional`. Аннотируй аргументы и возвраты. Зависимости — через `Annotated[..., Depends(...)]`.
- Импорты группируй по `ruff` (`I`): stdlib → сторонние → `app.*`.
- Кавычки одинарные (`ruff` `quote-style = single`).
- Имена: классы `PascalCase`, функции/переменные `snake_case`, константы `UPPER_SNAKE_CASE` в `app/constants.py`.
- Схемы — суффикс `Schema` (`UserReadSchema`). Модели — имя сущности (`User`, `Wish`).
- Логи только через `loguru.logger`: системные ошибки — `error`, бизнес-логика — `warning`/`info`.
- Комментарии в коде - на русском.

## БД (SQLAlchemy 2.0)
- `DeclarativeBase` + `Mapped` / `mapped_column`.
- Связи через `relationship()` + `back_populates`.
- Запросы в стиле `select()` / `scalars()` / `execute()`.
- Первичные ключи — `UUID` с `default=uuid4`.

## API
- Роутеры в `app/routers/`, через `APIRouter` с тегами из `app.dependencies`.
- Всегда указывай `response_model`.
- Ошибки — через `HTTPException`.
- Защита роутов — зависимость `get_current_user`.
- Перед изменением ресурса проверяй владельца (напр. `get_current_user_wish`).

## Интеграции
- **Firebase** — auth и пуши (`app/firebase.py`, `app/notifications.py`).
- **VK** — соц-авторизация и списки друзей (`app/vk.py`).
- **Sentry** — мониторинг ошибок (настройка в `app/main.py`).
- **Telegram** — алерты/логи через `app.utils.send_tg_channel_message`.

## Тесты
- Обязательно 100% покрытие для `app/` (проверяется в pre-commit).
- Если требуется использовать мок, то вместо этого отрефактори тестируемый код, чтобы он поддерживал тестирование без моков.

## Безопасность
- Никогда не коммить секреты и `.env`.
- Авторизация — Firebase-токен в заголовке `Authorization`.

## Git
- Никогда не делай сама `git push`. Делает только пользователь вручную.
