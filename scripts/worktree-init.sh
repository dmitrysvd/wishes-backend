#!/usr/bin/env bash
# Сделать git worktree рабочим из коробки: подтянуть gitignored артефакты
# основного чекаута, без которых не работают snapshot.sh (симлинк wishes-product)
# и тесты/приложение (.env). Симлинки, не копии — чтобы не расходились.
# Идемпотентно: существующие файлы/ссылки не трогает.
#
#   scripts/worktree-init.sh [<путь worktree>] [<путь основного чекаута>]
#
# Без аргументов: worktree = текущий каталог, основной чекаут — из `git worktree list`.
set -euo pipefail

WORKTREE="${1:-$(pwd)}"
MAIN="${2:-$(git -C "$WORKTREE" worktree list --porcelain | head -1 | sed 's/^worktree //')}"

if [ "$(realpath "$WORKTREE")" = "$(realpath "$MAIN")" ]; then
  echo "это основной чекаут ($MAIN), инициализировать нечего"
  exit 0
fi

link() {  # link <имя> <target>
  local name="$1" target="$2" path="$WORKTREE/$1"
  if [ -e "$path" ] || [ -L "$path" ]; then
    echo "  $name: уже есть, не трогаю"
  else
    ln -s "$target" "$path"
    echo "  $name -> $target"
  fi
}

echo "worktree-init: $WORKTREE (основной чекаут: $MAIN)"
# Шина wishes-product: тот же target, что у симлинка в основном чекауте.
if [ -L "$MAIN/wishes-product" ]; then
  link wishes-product "$(readlink -f "$MAIN/wishes-product")"
else
  echo "  wishes-product: в основном чекауте нет симлинка — пропускаю"
fi
# .env — ссылка на основной, а не копия.
if [ -f "$MAIN/.env" ]; then
  link .env "$MAIN/.env"
else
  echo "  .env: в основном чекауте нет — пропускаю"
fi
