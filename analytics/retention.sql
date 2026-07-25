-- Возврат и активность. Источник — user_activity_day (копится с момента выката
-- прибора; до первой полной недели данных срезы будут пустыми, это ожидаемо).
--
-- Определения зафиксированы здесь намеренно: DAU/WAU/MAU, посчитанные каждый раз
-- по-своему, между собой несравнимы. Правки — только осознанно и в этом файле.
--
-- Сид-юзеров dev/test-байпаса (фича 0009, `user.is_test`) исключаем везде — иначе
-- заходы с тестового токена подмешиваются в продуктовые метрики. Все прежние
-- срезы считались так же.
\pset pager off

-- Активность живых юзеров: база для всех запросов ниже.
CREATE TEMP VIEW activity AS
SELECT a.*
FROM user_activity_day a
JOIN "user" u ON u.id = a.user_id AND NOT u.is_test;

\echo === DAU/WAU/MAU (по факту активности, а не по last_login_at) ===
-- Окна полуоткрытые и привязаны к «сегодня»; сутки — UTC, как и в приборе.
SELECT
  count(DISTINCT user_id) FILTER (
    WHERE activity_date = CURRENT_DATE) AS dau,
  count(DISTINCT user_id) FILTER (
    WHERE activity_date > CURRENT_DATE - 7) AS wau,
  count(DISTINCT user_id) FILTER (
    WHERE activity_date > CURRENT_DATE - 30) AS mau
FROM activity;

\echo === Активные по дням (последние 30 суток) ===
SELECT activity_date, count(*) AS users, sum(request_count) AS requests
FROM activity
WHERE activity_date > CURRENT_DATE - 30
GROUP BY 1 ORDER BY 1;

\echo === Возврат: сколько РАЗНЫХ суток человек был активен ===
-- Ключевая метрика продукта: повод у него событийный, поэтому важна не глубина
-- визита, а само число возвращений.
SELECT active_days, count(*) AS users
FROM (SELECT user_id, count(*) AS active_days FROM activity GROUP BY 1) t
GROUP BY 1 ORDER BY 1;

\echo === Разрыв между визитами (медиана и максимум) ===
-- Насколько долгую паузу человек готов выдержать и всё-таки вернуться. Если
-- гипотеза «возврат под повод» верна, у части юзеров разрывы будут месячными.
WITH gaps AS (
  SELECT activity_date - lag(activity_date) OVER (
           PARTITION BY user_id ORDER BY activity_date) AS gap_days
  FROM activity
)
SELECT
  count(*) AS gaps_total,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_days) AS median_gap,
  max(gap_days) AS max_gap
FROM gaps WHERE gap_days IS NOT NULL;

\echo === Возврат по когортам регистрации ===
SELECT date_trunc('month', u.registered_at)::date AS cohort,
       count(DISTINCT u.id) AS users,
       count(DISTINCT a.user_id) AS seen_since_instrument,
       count(DISTINCT a.user_id) FILTER (
         WHERE a.activity_date > CURRENT_DATE - 30) AS active_30d
FROM "user" u
LEFT JOIN activity a ON a.user_id = u.id
WHERE NOT u.is_test
GROUP BY 1 ORDER BY 1 DESC;

\echo === Бёрздей-радар: адопшен ===
-- Открытия радара переживают ротацию логов, поэтому счётчик живёт в ролапе.
SELECT date_trunc('week', activity_date)::date AS week,
       count(DISTINCT user_id) FILTER (WHERE radar_open_count > 0) AS users_opened,
       sum(radar_open_count) AS opens,
       count(DISTINCT user_id) AS users_active
FROM activity
GROUP BY 1 ORDER BY 1;

\echo === Радар и возврат: возвращаются ли открывшие чаще ===
-- Осторожно: это КОРРЕЛЯЦИЯ. Рандомизации нет, радар выкачен всем сразу, так что
-- причинность отсюда не следует — открывают радар и так более вовлечённые.
WITH per_user AS (
  SELECT user_id, count(*) AS active_days, sum(radar_open_count) > 0 AS opened_radar
  FROM activity GROUP BY 1
)
SELECT opened_radar, count(*) AS users, round(avg(active_days), 2) AS avg_active_days
FROM per_user GROUP BY 1;
