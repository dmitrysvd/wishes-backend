#!/usr/bin/env bash
# Срез прод-таблиц для локального джойна с выгрузкой myTracker (схема
# `prod_snapshot` в локальной БД из DATABASE_URL). Прод — только чтение
# (COPY TO STDOUT), без PII: из `user` только id/даты/пол/is_test, токены
# установок не копируются. Каждый запуск пересоздаёт схему целиком — это
# снимок, а не реплика.
#
#   scripts/prod_snapshot.sh [<ssh-хост>]     # по умолчанию wishes
set -euo pipefail

HOST="${1:-wishes}"
cd "$(dirname "$0")/.."
DATABASE_URL=$(grep -E '^DATABASE_URL=' .env | cut -d= -f2- | tr -d "'\"")

remote_psql() {
  ssh "$HOST" 'docker exec -i wishes-db sh -c "psql -U \$POSTGRES_USER -d \$POSTGRES_DB -v ON_ERROR_STOP=1 -q"'
}

# Пары «локальная таблица ← запрос к проду». Колонки локальной таблицы = колонки запроса.
declare -A TABLES=(
  ["user"]='select id, registered_at, last_login_at, gender::text as gender, is_test from "user"'
  [push_sending_log]='select id, sent_at, reason::text as reason, reason_user_id, target_user_id, campaign_key, trigger::text as trigger, opened_at from push_sending_log'
  [user_activity_day]='select user_id, activity_date, first_seen_at, last_seen_at, request_count, radar_open_count from user_activity_day'
  [push_installation]='select id, user_id, saved_at, fid is not null as has_fid from push_installation'
  [user_following]='select follower_id, followed_id, created_at from user_following'
  [wish]='select id, user_id, reserved_by_id, is_active, is_archived, created_at, reserved_at, is_reservation_notification_sent, price_source::text as price_source from wish'
)

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q <<'SQL'
DROP SCHEMA IF EXISTS prod_snapshot CASCADE;
CREATE SCHEMA prod_snapshot;
CREATE TABLE prod_snapshot."user" (id uuid primary key, registered_at timestamp, last_login_at timestamp, gender text, is_test boolean);
CREATE TABLE prod_snapshot.push_sending_log (id uuid primary key, sent_at timestamp, reason text, reason_user_id uuid, target_user_id uuid, campaign_key text, trigger text, opened_at timestamp);
CREATE TABLE prod_snapshot.user_activity_day (user_id uuid, activity_date date, first_seen_at timestamptz, last_seen_at timestamptz, request_count int, radar_open_count int, primary key (user_id, activity_date));
CREATE TABLE prod_snapshot.push_installation (id uuid primary key, user_id uuid, saved_at timestamptz, has_fid boolean);
CREATE TABLE prod_snapshot.user_following (follower_id uuid, followed_id uuid, created_at timestamptz);
CREATE TABLE prod_snapshot.wish (id uuid primary key, user_id uuid, reserved_by_id uuid, is_active boolean, is_archived boolean, created_at timestamptz, reserved_at timestamptz, is_reservation_notification_sent boolean, price_source text);
SQL

for table in "${!TABLES[@]}"; do
  echo "\\copy (${TABLES[$table]}) to stdout with (format csv)" | remote_psql \
    | psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -c "\\copy prod_snapshot.\"$table\" from stdin with (format csv)"
  echo "prod_snapshot.$table: $(psql "$DATABASE_URL" -Atc "select count(*) from prod_snapshot.\"$table\"") строк"
done
echo "снимок снят: $(date -u +%FT%TZ)"
