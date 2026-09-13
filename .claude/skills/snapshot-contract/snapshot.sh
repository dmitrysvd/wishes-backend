#!/usr/bin/env bash
# Детерминированный снапшот OpenAPI из кода в шину. См. SKILL.md / PROTOCOL.md §5, §10.
#
#   snapshot.sh --candidate <NNNN-slug>   # кандидат до заморозки: features/<фича>/openapi.candidate.json
#   snapshot.sh --freeze <NNNN-slug>      # заморозка: корневой openapi.snapshot.json, кандидат удаляется
#   snapshot.sh                           # амендмент уже замороженного: только корневой файл
#
# Корневой файл — замороженный контракт, его меняет только заморозка/амендмент;
# кандидат живёт в папке фичи, чтобы аудит и заморозка не смешивались (§10).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DIR/../../.." && pwd)"   # корень бэк-репо
BUS="$ROOT/wishes-product"

MODE="${1:-amend}"
FEATURE="${2:-}"
case "$MODE" in
  --candidate|--freeze)
    [[ -n "$FEATURE" && -d "$BUS/features/$FEATURE" ]] \
      || { echo "нужна папка фичи: $MODE <NNNN-slug> (в $BUS/features)" >&2; exit 2; }
    ;;
  amend) ;;
  *) echo "usage: snapshot.sh [--candidate|--freeze <NNNN-slug>]" >&2; exit 2 ;;
esac

if [[ "$MODE" == "--candidate" ]]; then
  OUT="$BUS/features/$FEATURE/openapi.candidate.json"
else
  OUT="$BUS/openapi.snapshot.json"
fi

cd "$ROOT"
# -W ignore гасит UserWarning (напр. дубли operationId), но оставляет реальные ошибки
uv run python -W ignore - "$OUT" <<'PY'
import json, sys
from app.main import app

spec = app.openapi()
with open(sys.argv[1], 'w') as f:
    json.dump(spec, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write('\n')
print(
    f"снапшот снят: {len(spec.get('paths', {}))} путей, "
    f"{len(spec.get('components', {}).get('schemas', {}))} схем, "
    f"версия {spec['info'].get('version')}"
)
PY
echo "→ $OUT"

if [[ "$MODE" == "--freeze" ]]; then
  CANDIDATE="$BUS/features/$FEATURE/openapi.candidate.json"
  if [[ -f "$CANDIDATE" ]]; then
    rm -f "$CANDIDATE"
    echo "кандидат удалён: $CANDIDATE (закоммить удаление вместе со снапшотом)"
  fi
fi
