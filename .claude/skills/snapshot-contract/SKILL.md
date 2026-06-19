---
name: snapshot-contract
description: Снять детерминированный снапшот OpenAPI из кода бэка в общую шину (wishes-product/openapi.snapshot.json). Использовать на шаге заморозки контракта (agreed) и при амендменте. Не угадывай команду — запускай скрипт. См. wishes-product/PROTOCOL.md §10.
---

# Снапшот контракта (OpenAPI из кода)

Снимает контракт всего API из `app.openapi()` **без деплоя** и кладёт в шину
детерминированно (`sort_keys` — чтобы git-дифф был дельтой фичи, а не шумом порядка).
См. `wishes-product/PROTOCOL.md` §10.

```bash
.claude/skills/snapshot-contract/snapshot.sh
```

Пишет в `wishes-product/openapi.snapshot.json` (симлинк на шину), печатает число путей/схем.

## Когда

- **Заморозка фичи (`agreed`):** контракт дописан, чек-лист полноты закрыт в спеке, слепой
  аудит вернул ноль дыр.
- **Амендмент:** после правки спека — пересними и ре-заморозь.

## После снятия

1. В `intent.md` фичи: `status: agreed`, обнови `endpoints` и `updated`.
2. Закоммить снапшот в шину **отдельным коммитом** (дифф = дельта фичи). Push — человек.
3. Сверка с задеплоенным — скилл `verify-contract` в шине (после деплоя):
   `wishes-product/.claude/skills/verify-contract/verify.sh`.
