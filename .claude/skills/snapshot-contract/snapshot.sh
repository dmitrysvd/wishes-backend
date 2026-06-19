#!/usr/bin/env bash
# Детерминированный снапшот OpenAPI из кода в шину. См. SKILL.md / PROTOCOL.md §10.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DIR/../../.." && pwd)"   # корень бэк-репо
OUT="$ROOT/wishes-product/openapi.snapshot.json"

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
