# nginx (хост, не контейнер)

`hotelki.pro.conf` — трекаемый источник правды для серверного nginx. nginx живёт
**на хосте** (терминирует TLS через Certbot), в docker-compose его нет, поэтому
конфиг применяется вручную при изменениях.

## Что делает для OG-превью

Расшаренная ссылка `hotelki.pro/user?userId=...` обслуживается двояко по `User-Agent`:

- **краулер соцсети** (Telegram/VK/WhatsApp/FB/…) → проксируется на бэкенд `/og/user`,
  который отдаёт серверный HTML с Open Graph-тегами (превью-карточка в чате);
- **живой юзер** → как и раньше, Flutter SPA (`/data/www/index.html`).

Развилка — `map $http_user_agent $is_social_crawler` + `location = /user`
(идиома `error_page 418` → named location `@og_preview`, без `proxy_pass` внутри `if`).

## Что делает для аналитики

`location /api/v1/` пишет **второй** лог — `/var/log/nginx-analytics/analytics.log`,
формат `json_analytics` (см. `log_format` в конфиге). Общий `access.log` (combined,
с IP) остаётся как был — это ops-лог, его не трогаем.

Аналитический лог опирается на два заголовка ответа от бэка
(`app/helpers/activity.py`): `X-User-Id` и `X-Route`. Первый даёт «кто», второй —
**шаблон** роута (`/users/{user_id}/wishes`) вместо сырого пути, иначе каждый UUID
в URL создаёт свой бакет и лог не агрегируется. Оба снимаются `proxy_hide_header`,
то есть клиенту не уходят, но `$upstream_http_*` успевает их прочитать.

Горизонты разведены намеренно: nginx-лог — детальное **короткое** окно (воронка,
пути, тайминги), а долгий возврат (DAU/WAU/MAU, сезонность, адопшен радара) живёт
в таблице `user_activity_day`, которая переживает и ротацию логов, и редеплой.
Запросы к обоим — в `analytics/` в корне репозитория.

### Одноразовая установка на сервере (root): retention

Дефолтный `/etc/logrotate.d/nginx` даёт `daily` + `rotate 14`, то есть 14 дней —
для сезонного анализа мало. Аналитическому логу нужна своя ротация, но его нельзя
класть в `/var/log/nginx/`: тамошний паттерн `*.log` уже матчится общей секцией, и
второе правило на тот же файл — «duplicate log entry» в logrotate. Поэтому
отдельный каталог:

```bash
sudo install -d -o www-data -g adm -m 750 /var/log/nginx-analytics
sudo tee /etc/logrotate.d/nginx-analytics >/dev/null <<'EOF'
/var/log/nginx-analytics/analytics.log {
	daily
	missingok
	rotate 90
	compress
	delaycompress
	notifempty
	create 0640 www-data adm
	sharedscripts
	postrotate
		invoke-rc.d nginx rotate >/dev/null 2>&1
	endscript
}
EOF
sudo logrotate -d /etc/logrotate.d/nginx-analytics   # dry-run, без записи
```

Сжатый лог — десятки КБ в сутки, 90 дней стоят единицы МБ. Каталог нужно создать
**до** применения конфига: без него `nginx -t` упадёт, и `apply-nginx.sh`
откатится (что правильно, но деплой отработает вхолостую).

## Применение: автоматически из deploy.sh

`deploy.sh` на каждом деплое вызывает `apply-nginx.sh`, который **идемпотентно**
применяет `hotelki.pro.conf` из репо: сверяет с живым конфигом, и если изменился —
валидирует `nginx -t` и делает `reload`. При провале валидации откатывает конфиг и
reload не делает (битый конфиг не доезжает до боя). Если конфиг не менялся — no-op,
лишних reload нет.

`deploy.sh` запускается CI **неинтерактивно**, а правка `/etc/nginx` и reload нужны
от root — поэтому нужен беспарольный sudo на одну root-owned обёртку.

### Одноразовая установка на сервере (root)

`wishes` уже полный sudoer (с паролем) — NOPASSWD на узкую обёртку привилегий не
расширяет, лишь снимает пароль для автоматического пути. Обёртка ставится как
root-owned копия (не записываемая юзером `wishes` — иначе NOPASSWD стал бы дырой):

```bash
cd /home/wishes/wishes
sudo install -m 755 -o root -g root \
  deploy/nginx/apply-nginx.sh /usr/local/sbin/apply-nginx-wishes.sh
printf 'wishes ALL=(root) NOPASSWD: /usr/local/sbin/apply-nginx-wishes.sh\n' \
  | sudo tee /etc/sudoers.d/wishes-nginx
sudo chmod 440 /etc/sudoers.d/wishes-nginx
sudo visudo -c                       # проверка синтаксиса sudoers
sudo /usr/local/sbin/apply-nginx-wishes.sh   # первое применение
```

> Логика обёртки стабильна; меняется в основном сам `hotelki.pro.conf` (он
> применяется автоматически). Если правишь **`apply-nginx.sh`** — переустанови
> обёртку первой командой выше. До установки deploy.sh просто пропускает nginx-шаг.

### Применить вручную (без deploy.sh)

```bash
sudo /usr/local/sbin/apply-nginx-wishes.sh
```

## Проверить после применения

```bash
# краулер видит OG-карточку (200 + og:title):
curl -s -A 'Telegrambot' 'https://hotelki.pro/user?userId=<UUID>' | grep -i 'og:title'

# живой юзер по-прежнему получает SPA (index.html, без og:*):
curl -s -A 'Mozilla/5.0' 'https://hotelki.pro/user?userId=<UUID>' | grep -i '<title>'
```

Финальная валидация превью — дебаггерами платформ: Telegram (@WebpageBot),
VK (`vk.com/dev/pages.clearCache`), Facebook Sharing Debugger.
