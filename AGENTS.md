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
- **Hawk** (hawk.so) — трекер ошибок (`HAWK_TOKEN`, отправка из `internal_exception_handler` в `app/main.py`).

## Тесты
- Обязательно 100% покрытие для `app/` (проверяется в pre-commit).
- Если требуется использовать мок, то вместо этого отрефактори тестируемый код, чтобы он поддерживал тестирование без моков.

## Безопасность
- Никогда не коммить секреты и `.env`.
- Авторизация — Firebase-токен в заголовке `Authorization`.

## Совместная разработка через wishes-product

Полный процесс — в `wishes-product/PROTOCOL.md` (общая шина, симлинк `wishes-product/`).
Это источник правды; ниже — твоя роль: бэк = **автор контракта**.

- **Контракт = OpenAPI, code-first.** Пишешь Pydantic-схемы (`app/schemas.py`) и сигнатуры
  роутов; контракт = `app.openapi()`. Снапшот снимаешь **без деплоя** дампом в файл.
- **Проектируй от экранов.** Форму выводишь из `intent.md` фичи (раздел «Поведение и экраны»),
  а не из того, как удобно отдать. Контракт — публичный интерфейс (`app/schemas.py`), НЕ модели
  `app/db.py`: структуру БД наружу не выставляй.
- **Вписывай поведение в спек, не только форму:** `description` на операции (docstring), на
  каждом поле (`Field(description=...)`) и на **каждом** коде ответа (когда он возникает);
  `examples` на успех/ошибки/пусто/край; все `4xx` в `responses={...}` (assert-like `5xx` —
  вне контракта, фронт фолбэчит генериком); полные `enum`;
  `required` / `nullable` / опущено с явной семантикой; сайд-эффекты и воркфлоу — в
  `description` / `x-*`.
- **Гейт `agreed`** = чек-лист полноты контракта закрыт **в спеке** (PROTOCOL.md §7) и слепой
  аудит вернул ноль дыр. До этого логику эндпоинтов не пишешь (контракт-фёрст).
- **Заморозка:** сними снапшот скиллом `snapshot-contract`
  (`.claude/skills/snapshot-contract/snapshot.sh` — детерминированный дамп `app.openapi()` в
  `wishes-product/openapi.snapshot.json`, не угадывай команду); в `intent.md` ставишь
  `status: agreed`, обновляешь `endpoints` и `updated`; коммит отдельным шагом. Перенос
  снапшота в шину = ритуал заморозки. (Push в шину делает человек — нет credential в namespace.)
- **Severity вопросов:** `🔴 BLOCKING` — стоп по ветке, спроси человека живьём, на остальном
  работай; до человека неси только продуктовое, форму/семантику закрывай сам. `🟡 ASSUMED` —
  по дефолту, залогируй, не блокируйся.
- **Амендмент:** дыра, найденная после заморозки, — поправка спека (правишь → ре-`agreed` →
  вторая сторона перечитывает), а не задача «на потом».
- **Verify:** после деплоя сверь снапшот с прод-swagger скиллом `verify-contract`
  (`wishes-product/.claude/skills/verify-contract/verify.sh`).

## Git
- Никогда не делай сама `git push`. Делает только пользователь вручную.
