FROM python:3.10-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /uvx /bin/


WORKDIR /app

COPY pyproject.toml uv.lock ./
# --no-dev: в базовый (и значит прод) образ не тянем pytest/ruff/ty.
RUN uv sync --locked --no-dev

COPY app/ ./app

COPY alembic.ini ./
COPY alembic/ ./alembic

# Dev-образ для devcontainer: инструменты разработки и пользователь vscode
# с passwordless-sudo. В прод НЕ идёт — прод собирается из стадии prod ниже
# (target: prod в .github/workflows/ci.yml).
FROM base AS dev

RUN apt-get update \
    && apt-get install -y git vim \
    && rm -rf /var/lib/apt/lists/*

# Доустанавливаем dev-зависимости (pytest, ruff, ty) поверх базовых.
RUN uv sync --locked

RUN groupadd vscode \
    && useradd -m vscode -g vscode -p "" \
    && mkdir /etc/sudoers.d \
    && echo vscode ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/vscode \
    && chmod 0440 /etc/sudoers.d/vscode

# Прод-образ: только зависимости и код, без dev-инструментов и без vscode-юзера.
# Последняя стадия по умолчанию: сборка без target даёт прод, а не dev.
FROM base AS prod
