---
name: snapshot-contract
description: Снять детерминированный снапшот OpenAPI из кода бэка в общую шину — кандидат в папку фичи (до аудита) или замороженный контракт в корень (agreed, амендмент). Не угадывай команду — запускай скрипт. См. wishes-product/PROTOCOL.md §5, §10.
---

# Снапшот контракта (OpenAPI из кода)

Снимает контракт всего API из `app.openapi()` **без деплоя** и кладёт в шину
детерминированно (`sort_keys` — чтобы git-дифф был дельтой фичи, а не шумом порядка).
См. `wishes-product/PROTOCOL.md` §5 (кандидат), §10 (заморозка).

```bash
.claude/skills/snapshot-contract/snapshot.sh --candidate 0013-price-alerts  # до аудита
.claude/skills/snapshot-contract/snapshot.sh --freeze 0013-price-alerts     # заморозка
.claude/skills/snapshot-contract/snapshot.sh                                # амендмент
```

- `--candidate` пишет `wishes-product/features/<фича>/openapi.candidate.json`; корневой
  снапшот не трогается — аудит гоняется по кандидату.
- `--freeze` пишет корневой `wishes-product/openapi.snapshot.json` и удаляет кандидат
  фичи: снапшот + удаление кандидата + `status: agreed` — один коммит заморозки.
- Без аргументов — амендмент уже замороженного контракта (только корневой файл).

## После снятия

1. Кандидат: обнови `endpoints` во frontmatter `intent.md`, статус не меняй; коммит
   в шину **только по путям** (`git add <файлы>`, никогда `-A`) — шина общая.
2. Заморозка: в `intent.md` — `status: agreed`, `updated`; коммит отдельным шагом
   (дифф = дельта фичи). Push — человек.
3. Сверка с задеплоенным — скилл `verify-contract` в шине (после деплоя):
   `wishes-product/.claude/skills/verify-contract/verify.sh`.
