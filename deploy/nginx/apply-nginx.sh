#!/usr/bin/bash
# Идемпотентно применяет nginx-конфиг hotelki.pro из репозитория.
#
# Запускается из deploy.sh через `sudo` (NOPASSWD на root-owned копию этого
# скрипта в /usr/local/sbin — см. deploy/nginx/README.md). Должен быть root-owned
# и не записываемым юзером wishes: иначе NOPASSWD на записываемый скрипт = дыра.
#
# Безопасность: reload делается ТОЛЬКО после успешного `nginx -t`; при провале
# валидации конфиг откатывается на предыдущий и reload не выполняется. Если конфиг
# не изменился — ничего не делаем (нет лишних reload на каждый деплой приложения).
set -euo pipefail

SRC=/home/wishes/wishes/deploy/nginx/hotelki.pro.conf
DST=/etc/nginx/sites-enabled/wishes

if cmp -s "$SRC" "$DST"; then
  echo "nginx: конфиг не изменился — пропуск"
  exit 0
fi

echo "nginx: конфиг изменился — валидация и применение"
backup=$(mktemp)
cp "$DST" "$backup"
cp "$SRC" "$DST"
if ! nginx -t; then
  echo "nginx: 'nginx -t' ПРОВАЛИЛСЯ — откат конфига, reload НЕ делаю" >&2
  cp "$backup" "$DST"
  rm -f "$backup"
  exit 1
fi
rm -f "$backup"
systemctl reload nginx
echo "nginx: конфиг применён, выполнен reload"
