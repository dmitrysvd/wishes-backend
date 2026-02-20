FROM python:3.10-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./

COPY app/ ./app

FROM base AS dev

RUN apt-get update && apt-get install -y git vim sqlite3 pgloader && rm -rf /var/lib/apt/lists/*
RUN groupadd vscode \
    && useradd -m vscode -g vscode -p "" \
    && mkdir /etc/sudoers.d \
    && echo vscode ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/vscode \
    && chmod 0440 /etc/sudoers.d/vscode
RUN uv sync --locked
