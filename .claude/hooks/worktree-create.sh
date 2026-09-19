#!/usr/bin/env bash
# Хук WorktreeCreate (Claude Code): заменяет стандартное создание worktree —
# делает `git worktree add` и сразу инициализирует его scripts/worktree-init.sh,
# чтобы snapshot.sh и pytest работали без ручных шагов. На stdin — JSON с
# base_path и worktree_path; на stdout обязан вернуть путь созданного worktree.
# Всё остальное — в stderr, иначе Claude Code примет вывод за путь.
set -euo pipefail

input=$(cat)
# Поля входа зависят от версии Claude Code: без base_path берём каталог проекта
# из окружения, без worktree_path — стандартное место `.claude/worktrees/<name>`.
base_path=$(jq -r '.base_path // empty' <<<"$input")
base_path="${base_path:-${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}}"
worktree_path=$(jq -r '.worktree_path // empty' <<<"$input")
if [ -z "$worktree_path" ]; then
  name=$(jq -r '.name // empty' <<<"$input")
  [ -n "$name" ] || { echo "WorktreeCreate: во входе нет ни worktree_path, ни name: $input" >&2; exit 1; }
  worktree_path="$base_path/.claude/worktrees/$name"
fi
branch=$(basename "$worktree_path")

mkdir -p "$(dirname "$worktree_path")"
if git -C "$base_path" show-ref --quiet --verify "refs/heads/$branch"; then
  git -C "$base_path" worktree add "$worktree_path" "$branch" >&2
else
  git -C "$base_path" worktree add -b "$branch" "$worktree_path" HEAD >&2
fi
"$base_path/scripts/worktree-init.sh" "$worktree_path" "$base_path" >&2
echo "$worktree_path"
