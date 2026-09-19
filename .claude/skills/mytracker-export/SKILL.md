---
name: mytracker-export
description: Выгрузить сырые данные myTracker (установки, сессии, кастомные события Android-клиента) в локальную БД и сджойнить с прод-срезом (пуши, активность, юзеры). Используй для вопросов про версии клиента, GMS, сессии после пуша — всего, чего нет в БД бэка. Не зови Export API руками — запускай скрипт.
---

# Выгрузка myTracker

myTracker стоит только в Android-клиенте; `customUserId` = наш `user.id`
(ставится после логина и **не сбрасывается при выходе** — устройство после логаута
продолжает числиться за последним юзером). Кастомные события: `mt_login`, `gms_available`.

## Запуск

Ключи `MYTRACKER_API_USER_ID` / `MYTRACKER_API_SECRET` — в `.env` (кабинет myTracker →
API). Локальная БД — `DATABASE_URL` из `.env`.

```bash
uv run python -m scripts.mytracker_export --kind sessions --kind events --kind installs \
    --from 2026-08-20 --to 2026-09-19
scripts/prod_snapshot.sh          # срез прод-таблиц для джойна (read-only на проде)
```

Скрипт создаёт запрос в Export API, ждёт готовности (минуты), скачивает csv.gz и
заливает в схему `mytracker`. Окно уже залито → пропуск, `--force` для повтора;
незавершённый запрос с тем же окном переиспользуется (в myTracker лимит
одновременных запросов). Повторная заливка строк не дублирует — ключ (устройство, время).

## Что где лежит

| схема.таблица | ключ | поля |
|---|---|---|
| `mytracker.installs` | `id_profile, event_at` | `user_id`, `installed_at`, `app_version`, `os_version`, `device_model`, `manufacturer`, `country`, `sdk_version` |
| `mytracker.sessions` | `id_profile, event_at` | `user_id`, `duration` (−1 = оборванная), `app_version`, `os_version` |
| `mytracker.events` | `id_profile, event_at, event_name` | `user_id`, `event_name`, `event_value`, `app_version` |
| `mytracker.export_request` | `id_raw_export` | журнал запросов: `kind`, окно, `status` (`pending`/`loaded`/`failed`) |
| `prod_snapshot.*` | как в проде | `user` (id, даты, пол, is_test), `push_sending_log`, `user_activity_day`, `push_installation` (без токенов), `user_following`, `wish` (без названий) |

`user_id` — распарсенный `customUserId` (NULL, если не UUID; сырое — в
`custom_user_id`). `event_at` — UTC. `app_version` — как отдаёт SDK (`1.1.16`).
`id_profile` = устройство+приложение; один юзер может быть на нескольких.

## Джойн

Точка склейки — `user_id`. «Вернулся ли получатель пуша»:

```sql
select p.campaign_key, count(*) sends,
  count(*) filter (where exists (
    select 1 from mytracker.sessions s
    where s.user_id = p.target_user_id
      and s.event_at between p.sent_at at time zone 'UTC'
                         and p.sent_at at time zone 'UTC' + interval '3 days')) returned_3d
from prod_snapshot.push_sending_log p
where p.reason = 'PRICE_ALERT' group by 1;
```

`push_sending_log.sent_at` — naive (по факту UTC), `event_at` — timestamptz: приводи
явно. Версия клиента юзера — его последняя сессия/установка по `event_at`.

## Ограничения

- Только Android; web/iOS в данных нет. «Нет сессий» ≠ «не открывал».
- `customUserId` липнет к устройству после логаута — для точных когорт бери
  `mt_login` как границу.
- Export API отдаёт данные с задержкой до ~3 часов; окно «до сегодня» неполное.
- Квоты: 100 запросов/мин; на 429 скрипт ждёт сам, параллельно не запускай.
