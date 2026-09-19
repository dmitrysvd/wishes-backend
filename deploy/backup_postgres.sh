#!/bin/bash
set -euo pipefail

# Бэкап Postgres из docker-контейнера wishes-db в синкаемую папку.
# Креды берём из окружения самого контейнера, чтобы не хранить их в скрипте.
#
# Запускается ночным cron'ом от юзера wishes:
#   0 3 * * * /home/wishes/backup_postgres.sh >> /home/wishes/logs/backup.log 2>&1
#
# Источник правды — этот файл в репозитории; на сервере лежит его копия
# (установка пока ручная, см. deploy/README-heartbeat.md).

backup_dir="/home/wishes/Sync/wishes_db_backups"
container="wishes-db"
retention_days=14
name_glob='wishes_db_*.sql.gz'   # ротация трогает ТОЛЬКО файлы по этому шаблону
# Heartbeat-отметка успеха: её свежесть отдаётся наружу через
# /health/heartbeat/backup, и внешний монитор краснеет, если бэкапа не было
# больше 26 часов. Пишется ПОСЛЕДНЕЙ — до неё скрипт должен пройти целиком.
heartbeat_file="/data/heartbeats/backup"

# --- Защита от дурака ---------------------------------------------------
# Каталог обязан быть непустым, абсолютным и лежать строго внутри
# ожидаемого места. Иначе (пустая/битая переменная) — аварийно выходим,
# НИЧЕГО не удаляя. Sync содержит важные файлы — это критично.
case "$backup_dir" in
  /home/wishes/Sync/wishes_db_backups) : ;;
  *) echo "FATAL: неожиданный backup_dir='$backup_dir', выход" >&2; exit 1 ;;
esac
if [ -z "${retention_days//[0-9]/}" ] && [ -n "$retention_days" ]; then : ; else
  echo "FATAL: retention_days должно быть числом, получено '$retention_days'" >&2; exit 1
fi
# ------------------------------------------------------------------------

mkdir -p "$backup_dir"
[ -d "$backup_dir" ] || { echo "FATAL: каталог не существует: '$backup_dir'" >&2; exit 1; }

timestamp=$(date "+%Y-%m-%d_%H-%M-%S")
out="$backup_dir/wishes_db_${timestamp}.sql.gz"

docker exec "$container" sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  | gzip > "$out"

# Защита от «пустого» бэкапа (например, если pg_dump упал)
if [ ! -s "$out" ]; then
  echo "ERROR: бэкап пустой, удаляю: $out" >&2
  rm -f "$out"
  exit 1
fi

# Целостность архива. Непустой файл ещё не значит читаемый: оборванный pg_dump
# или кончившееся на диске место дают битый gzip, который обнаружился бы только
# при восстановлении. `gunzip -t` прогоняет распаковку без записи на диск.
if ! gunzip -t "$out"; then
  echo "ERROR: архив битый (gunzip -t), удаляю: $out" >&2
  rm -f "$out"
  exit 1
fi

echo "Postgres backup created: $out ($(du -h "$out" | cut -f1))"

# Ротация: только файлы по шаблону, без рекурсии (-maxdepth 1), только файлы.
find "$backup_dir" -maxdepth 1 -type f -name "$name_glob" -mtime +"$retention_days" -delete

# Отметка успеха — последним шагом. Любой сбой выше валит скрипт из-за
# `set -e`, отметка не обновляется, и через 26 часов монитор поднимает тревогу.
mkdir -p "$(dirname "$heartbeat_file")"
touch "$heartbeat_file"
echo "Heartbeat обновлён: $heartbeat_file"
