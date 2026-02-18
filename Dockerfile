FROM python:3.10-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./

COPY app/ ./app

FROM base AS dev

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
RUN uv sync --locked
