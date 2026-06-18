#!/usr/bin/bash

set -e

export PATH="/home/wishes/.local/bin:$PATH"  # на случай запуска через ssh

# Тег образа для деплоя: SHA из CI (иммутабельный) либо latest при ручном запуске.
# Для отката: bash deploy.sh <старый-sha>
export WISHES_TAG="${1:-latest}"

cd ~/wishes
git switch master
git pull

cp /home/wishes/wishes/static/* /data/static -r

echo "Деплой образа: dmitrysvd1/wishes-app:${WISHES_TAG}"
docker compose pull app

echo "Пересоздание db (подхватывает изменения command/конфига, напр. shared_preload_libraries)"
docker compose up -d db

echo "Применение миграций"
docker compose run --rm app uv run alembic upgrade head

echo "Запуск контейнеров"
docker compose up -d --remove-orphans

echo "Очистка старых контейнеров"
docker image prune -f
