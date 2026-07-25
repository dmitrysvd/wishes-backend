-- Резервации во времени. Источник — wish.reserved_at (копится с момента выката;
-- у резерваций, сделанных раньше, там NULL — легаси, всегда фильтруем явно).
--
-- Зачем: резерв — самое ценное действие в продукте (ради него ссылку и
-- пересылают), но до этой инструментации он не был привязан ко времени, и
-- проверить «дал ли повод прирост подарков» было нечем.
\pset pager off

-- Сид-юзеров dev/test-байпаса (фича 0009) исключаем, как и в retention.sql.
CREATE TEMP VIEW reserved AS
SELECT w.*
FROM wish w
JOIN "user" u ON u.id = w.reserved_by_id AND NOT u.is_test
WHERE w.reserved_at IS NOT NULL;

\echo === Резервации по неделям ===
SELECT date_trunc('week', reserved_at)::date AS week,
       count(*) AS reservations,
       count(DISTINCT reserved_by_id) AS givers
FROM reserved
GROUP BY 1 ORDER BY 1;

\echo === Легаси-доля: сколько резерваций без времени ===
SELECT count(*) FILTER (WHERE reserved_at IS NULL) AS legacy_without_time,
       count(*) FILTER (WHERE reserved_at IS NOT NULL) AS instrumented
FROM wish WHERE reserved_by_id IS NOT NULL;

\echo === Резерв по follow-ребру или мимо графа ===
-- 68% резерваций исторически шли по рёбрам подписки — ребро материализуется в
-- подарок. Смотрим, держится ли доля после выката радара (радар даёт повод и
-- тем, кто НЕ подписан: VK-друг без ребра).
SELECT EXISTS (
         SELECT 1 FROM user_following uf
         WHERE uf.follower_id = w.reserved_by_id AND uf.followed_id = w.user_id
       ) AS via_follow_edge,
       count(*) AS reservations
FROM reserved w
GROUP BY 1;

\echo === Резерв рядом с открытием радара (в те же сутки) ===
-- Грубая воронка на данных ролапа: точную последовательность внутри визита
-- смотреть в nginx-логе (analytics.log), он живёт 90 дней.
SELECT count(*) AS reservations_same_day_as_radar_open
FROM reserved w
JOIN user_activity_day a
  ON a.user_id = w.reserved_by_id
 AND a.activity_date = (w.reserved_at AT TIME ZONE 'UTC')::date
WHERE a.radar_open_count > 0;
