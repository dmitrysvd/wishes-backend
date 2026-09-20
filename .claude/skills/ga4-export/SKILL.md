---
name: ga4-export
description: Снять срез Firebase/GA4 Analytics (активные по платформе, события, login по методу, screen_view по экранам) в markdown для usage-analysis или проверить, что события с клиента доходят (Realtime). Используй для вопросов про поведение внутри сессии на всех платформах — того, чего нет ни в БД бэка, ни в myTracker (Android-only). Не зови Data API руками — запускай скрипт.
---

# Срез GA4 (Firebase Analytics)

Единственный клиентский счётчик на всех платформах (фича 0022): веб, Android,
iOS-PWA (в GA4 она `web`). События: `login` (`method`, `is_auto`), `screen_view`
(имя экрана из индекса `PRODUCT.md`), `setUserId` = наш `user.id`. Тест-сборка
не шлёт ничего. Абсолютные цифры — с сервера (`ANALYTICS.md`); GA4 даёт доли и
пути с поправкой на блокировщики.

## Запуск

`GA4_PROPERTY_ID` и `GA4_KEY_PATH` — в `.env` (см. `app/config.py`). Без
`GA4_KEY_PATH` берётся `FIREBASE_KEY_PATH`.

```bash
uv run python -m scripts.ga4_export --from 2026-09-01 --to 2026-09-19 > /tmp/ga4.md
uv run python -m scripts.ga4_export --realtime      # последние 30 минут
```

Вывод — markdown в stdout, секция на отчёт; вставляй в `usage-analysis-*.md`
как есть. Ошибка одного отчёта печатается под его заголовком (`⚠️ …`), остальные
выполняются.

## Что искать по ошибкам

- `PERMISSION_DENIED` — сервисному аккаунту не выдали Viewer на property или в
  GCP-проекте не включён Google Analytics Data API.
- `customEvent:method is not a valid dimension` — параметры `method`/`is_auto` не
  зарегистрированы как custom dimensions (GA4 Admin → Custom definitions); после
  регистрации данные копятся только вперёд.
- Пустые таблицы за вчера-сегодня — норма: стандартные отчёты отстают на 1–2 дня,
  для «пошло ли после деплоя» — `--realtime`.

## Ограничения

- `activeUsers` GA4 ≠ `user_activity_day`: разная методика и недосчёт из-за
  блокировщиков. Сравнивай только доли между платформами/экранами.
- Realtime не отдаёт `totalUsers`/`newUsers`/`sessions` — только счётчики событий и
  активных.
- Гость до входа и он же после входа — разные пользователи (склейка вне скоупа 0022).
- Старая закешированная веб-сборка шлёт старый набор событий до обновления.
