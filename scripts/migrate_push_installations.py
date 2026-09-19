"""Разовый перенос адресов пушей из `user.firebase_push_token` в установки
(фича 0016). Логика и правила — `app.push_installations.migrate_legacy_tokens`.

Каждый адрес проверяется dry-run отправкой в FCM (ничего не доставляется):
FID — гипотеза по префиксу токена, подтверждается только так. Идемпотентно:
юзеры с установками пропускаются, повторный прогон дочищает `unknown`.

Запуск на сервере: `python scripts/migrate_push_installations.py [--dry-run]`.
"""

import argparse

from app.db import SessionLocal
from app.logging import logger
from app.push_installations import migrate_legacy_tokens


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='только отчёт')
    args = parser.parse_args()
    with SessionLocal() as db:
        report = migrate_legacy_tokens(db, dry_run=args.dry_run)
    logger.info(
        'Перенос адресов{mode}: с FID {with_fid}, только токен {token_only}, '
        'мёртвых {dead}, неизвестно (повторить) {unknown}',
        mode=' (dry-run)' if args.dry_run else '',
        with_fid=len(report.with_fid),
        token_only=len(report.token_only),
        dead=len(report.dead),
        unknown=len(report.unknown),
    )
    for user_id in report.unknown:
        logger.warning('Не проверен, повторить прогон: {user_id}', user_id=user_id)


if __name__ == '__main__':
    main()
