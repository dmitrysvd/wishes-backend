FROM python:3.10-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /uvx /bin/


WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked

RUN apt-get update \
    && apt-get install -y git vim sqlite3 pgloader \
    && rm -rf /var/lib/apt/lists/*

COPY app/ ./app

COPY alembic.ini ./
COPY alembic/ ./alembic
COPY migration.load migrate_to_pg.sh migrate_to_pg.py ./

FROM base AS dev

RUN groupadd vscode \
    && useradd -m vscode -g vscode -p "" \
    && mkdir /etc/sudoers.d \
    && echo vscode ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/vscode \
    && chmod 0440 /etc/sudoers.d/vscode
