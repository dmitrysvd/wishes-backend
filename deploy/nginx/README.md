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
