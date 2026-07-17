"""Разовый бэкфилл: перенос соц-аватарок с внешнего CDN (VK, Google) на диск.

Зачем: почти все аватарки — хотлинки на `sun*.userapi.com` / `googleusercontent.com`.
VK-ссылки протухают, доступность чужого CDN вне нашего контроля, а каждый рендер
чужого профиля светит IP пользователя в VK/Google. Качаем картинку один раз к себе
и дальше отдаём через `/media`.

Не трогаем:
- фото, загруженные пользователем вручную (`photo_is_custom=True`);
- уже перенесённые на диск (`photo_path` заполнен) — идемпотентность повторного прогона.

Протухшие ссылки (404/сетевая ошибка) обнуляем в `photo_url` → фронт падает на
инициалы; каждую логируем — это заодно и замер, сколько VK-ссылок уже мертвы.

Запуск на сервере: `python -m app.cron_scripts.backfill_profile_images`.
"""

import httpx
from sqlalchemy import select

from app.db import SessionLocal, User
from app.helpers import download_avatar_bytes, save_profile_image_bytes
from app.logging import logger

# Таймаут на скачивание одной аватарки, секунды.
DOWNLOAD_TIMEOUT_SECONDS = 15


def backfill_user_image(
    user: User, client: httpx.Client, *, dry_run: bool = False
) -> bool:
    """Скачать аватарку пользователя и положить на диск.

    Возвращает True, если аватарка доступна (в обычном режиме — перенесена на
    диск). При ошибке скачивания в обычном режиме обнуляет `photo_url` (битую
    ссылку наружу не отдаём) и возвращает False. В `dry_run` ничего не пишет —
    только скачивает, чтобы отличить живые ссылки от протухших.
    """
    # photo_url не None — гарантировано фильтром выборки в main().
    assert user.photo_url is not None
    content = download_avatar_bytes(user.photo_url, client)
    if content is None:
        if not dry_run:
            logger.warning('Обнуляю битую аватарку: user={user_id}', user_id=user.id)
            user.photo_url = None
        return False
    if dry_run:
        logger.info('[dry-run] Перенёс бы аватарку: user={user_id}', user_id=user.id)
        return True
    save_profile_image_bytes(user, content, is_custom=False)
    logger.info('Аватарка перенесена на диск: user={user_id}', user_id=user.id)
    return True


def main(client: httpx.Client | None = None, *, dry_run: bool = False) -> None:
    logger.info(
        'Бэкфилл соц-аватарок на диск запущен{suffix}',
        suffix=' (DRY-RUN, без записи)' if dry_run else '',
    )
    own_client = client is None
    if client is None:
        client = httpx.Client(timeout=DOWNLOAD_TIMEOUT_SECONDS)
    migrated = 0
    failed = 0
    try:
        with SessionLocal() as db:
            # Только внешние соц-аватарки: не на диске и не кастомные.
            users = db.scalars(
                select(User).where(
                    User.photo_path.is_(None),
                    User.photo_url.is_not(None),
                    User.photo_is_custom.is_(False),
                )
            ).all()
            logger.info('К переносу: {n} аватарок', n=len(users))
            for user in users:
                if backfill_user_image(user, client, dry_run=dry_run):
                    migrated += 1
                else:
                    failed += 1
                if not dry_run:
                    db.add(user)
            if not dry_run:
                db.commit()
    finally:
        if own_client:
            client.close()
    logger.info(
        'Бэкфилл завершён{suffix}: доступно={migrated}, битых={failed}',
        suffix=' (dry-run, ничего не записано)' if dry_run else '',
        migrated=migrated,
        failed=failed,
    )


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='Перенос соц-аватарок с внешнего CDN на диск.'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Только показать, что было бы сделано, без записи на диск и в БД.',
    )
    main(dry_run=parser.parse_args().dry_run)
