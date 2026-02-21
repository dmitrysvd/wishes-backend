#!/usr/bin/bash

set -e

sqlite3 db.sqlite "PRAGMA foreign_keys = ON;"
sqlite3 db.sqlite "PRAGMA foreign_key_check;"

sqlite3 db.sqlite <<EOF
-- Показать что будет удалено
SELECT 'user_following', COUNT(*) FROM user_following
WHERE followed_id NOT IN (SELECT id FROM user);

SELECT 'push_sending_log', COUNT(*) FROM push_sending_log
WHERE reason_user_id NOT IN (SELECT id FROM user);

SELECT 'wish', COUNT(*) FROM wish
WHERE user_id NOT IN (SELECT id FROM user);

-- Удалить
DELETE FROM user_following
WHERE followed_id NOT IN (SELECT id FROM user);

DELETE FROM push_sending_log
WHERE reason_user_id NOT IN (SELECT id FROM user);

DELETE FROM wish
WHERE user_id NOT IN (SELECT id FROM user);

EOF

pgloader migration.load