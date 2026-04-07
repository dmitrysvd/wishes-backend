# Agent Guidelines for Wishes Project

This document provides essential information for AI coding agents working on the "Wishes" (Хотелки) project.

## 🛠 Tech Stack
- **Framework:** FastAPI
- **Database:** PostgreSQL (production & tests)
- **ORM:** SQLAlchemy 2.0 (using `Mapped` and `mapped_column`)
- **Migrations:** Alembic
- **Task Management:** `uv` (replaces `pip`, `venv`, etc.)
- **Auth:** Firebase Admin SDK, VK Integration
- **Formatting/Linting:** `ruff`, `pyright`
- **Testing:** `pytest` with `pytest-mock` and `fastapi.testclient.TestClient`

## 🚀 Common Commands

### Environment Setup
- `uv sync` - Install all dependencies
- `uv run uvicorn app.main:app --reload` - Start development server

### Quality Control
- `uv run ruff format .` - Format code
- `uv run ruff check --fix .` - Lint code and fix simple issues
- `uv run pyright` - Run static type checking
- `uv run pre-commit run --all-files` - Run all pre-commit hooks
- When creating a commit, if pre-commit fails, fix the reported issues and retry until the commit succeeds.

### Testing
- `uv run pytest` - Run all tests
- `uv run pytest app/test_main.py` - Run specific test file
- `uv run pytest app/test_main.py::TestMyWishes` - Run specific test class
- `uv run pytest app/test_main.py::TestMyWishes::test_list_wishes` - Run single test

### Database & Migrations
- `uv run alembic revision --autogenerate -m "description"` - Create new migration
- `uv run alembic upgrade head` - Apply migrations to database

## 📝 Code Style & Conventions

### 1. Imports
Follow `ruff` grouping (controlled by `I` rules):
1. Standard library
2. Third-party packages
3. Local application imports (using `app.` prefix)

### 2. Typing
- Use modern Python 3.10+ type hints: `str | None` instead of `Optional[str]`.
- Always type hint function arguments and return values.
- Use `Annotated` for FastAPI dependencies (e.g., `db: Annotated[Session, Depends(get_db)]`).

### 3. Naming Conventions
- **Classes:** `PascalCase`
- **Functions & Variables:** `snake_case`
- **Constants:** `UPPER_SNAKE_CASE` (placed in `app/constants.py` or module root)
- **Schemas:** Always suffix with `Schema` (e.g., `UserReadSchema`, `WishWriteSchema`).
- **Models:** Direct entity name (e.g., `User`, `Wish`).

### 4. Database (SQLAlchemy 2.0)
- Use `DeclarativeBase` and `Mapped` types.
- Define relationships using `relationship()` and `back_populates`.
- Use `select()`, `scalars()`, and `execute()` style queries.
- Primary keys should be `UUID` (using `uuid4` default).

### 5. API Design
- Place routers in `app/routers/`.
- Use `APIRouter` with tags from `app.dependencies`.
- Always specify `response_model` in route decorators.
- Use `HTTPException` for error responses.

### 6. Error Handling & Logging
- Use `loguru.logger` for all logging.
- Catch specific exceptions and raise `HTTPException` with clear detail.
- Sensitive errors should be logged at `error` level; business logic issues at `warning` or `info`.

## 📂 Project Structure
- `app/main.py`: Application entry point and middleware.
- `app/db.py`: Database models and engine setup.
- `app/schemas.py`: Pydantic models for request/response validation.
- `app/routers/`: Endpoint definitions grouped by entity.
- `app/dependencies.py`: Shared FastAPI dependencies (auth, db).
- `app/helpers/`: Small utility functions.
- `app/cron_scripts/`: Background tasks and scheduled jobs.

## 🔌 Integrations & Services
- **Firebase:** Used for authentication and push notifications (`app/firebase.py`, `app/notifications.py`).
- **VK:** Integration for social auth and friend lists (`app/vk.py`).
- **Sentry:** Error tracking and performance monitoring (configured in `app/main.py`).
- **Telegram:** used for alerts/logs via `app.utils.send_tg_channel_message`.

## 🔒 Security
- Never commit secrets or `.env` files.
- Authorization is handled via Firebase tokens in the `Authorization` header.
- Use `get_current_user` dependency to protect routes.
- Verify ownership before modifying resources (e.g., `get_current_user_wish`).

## 🧪 Testing Guidelines
- Use `pytest` fixtures for database and user setup.
- Override dependencies in tests using `app.dependency_overrides`.
- Mock external API calls (Firebase, VK, Telegram) using `pytest-mock`.
- Test data should be cleaned up after each test (handled by `db` fixture with `drop_all` or transaction rollback).
