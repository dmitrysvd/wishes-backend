#!/usr/bin/bash

set -e

# Path to SQLite database.
# Default: /app/db.sqlite (Docker volume mount).
# Override: pass as first argument, e.g. ./migrate_to_pg.sh ./local.sqlite
SQLITE_DB="${1:-/app/db.sqlite}"

if [ ! -f "$SQLITE_DB" ]; then
    echo "Error: SQLite database not found: $SQLITE_DB"
    echo "Usage: $0 [path/to/db.sqlite]"
    exit 1
fi

# Load .env if DATABASE_URL is not already in the environment.
if [ -z "$DATABASE_URL" ] && [ -f "$(dirname "$0")/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$(dirname "$0")/.env"
    set +a
fi

echo "=== SQLite source: $SQLITE_DB ==="

echo "--- Checking foreign key integrity ---"
sqlite3 "$SQLITE_DB" "PRAGMA foreign_keys = ON;"
sqlite3 "$SQLITE_DB" "PRAGMA foreign_key_check;"

echo "--- Cleaning orphaned records ---"
sqlite3 "$SQLITE_DB" <<EOF
SELECT 'user_following (follower) orphans', COUNT(*) FROM user_following
WHERE follower_id NOT IN (SELECT id FROM user);

SELECT 'user_following (followed) orphans', COUNT(*) FROM user_following
WHERE followed_id NOT IN (SELECT id FROM user);

SELECT 'push_sending_log orphans', COUNT(*) FROM push_sending_log
WHERE reason_user_id NOT IN (SELECT id FROM user)
   OR target_user_id NOT IN (SELECT id FROM user);

SELECT 'wish orphans', COUNT(*) FROM wish
WHERE user_id NOT IN (SELECT id FROM user);

DELETE FROM user_following
WHERE follower_id NOT IN (SELECT id FROM user)
   OR followed_id NOT IN (SELECT id FROM user);

DELETE FROM push_sending_log
WHERE reason_user_id NOT IN (SELECT id FROM user)
   OR target_user_id NOT IN (SELECT id FROM user);

DELETE FROM wish
WHERE user_id NOT IN (SELECT id FROM user);
EOF

echo "--- Starting PostgreSQL migration ---"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
uv run python "$SCRIPT_DIR/migrate_to_pg.py" "$SQLITE_DB"

echo "=== Migration complete ==="
