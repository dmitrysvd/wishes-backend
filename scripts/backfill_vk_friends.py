"""Разовый бэкфилл: пересобрать снимок VK-друзей (`vk_friends_data`) у существующих
пользователей с сохранённым access token.

Зачем: снимок VK-друзей раньше собирался один раз при первом входе и без фото
(запрашивали только `bdate`). Теперь `friends.get` тянет и `photo_100`, и снимок
освежается на каждом входе — но у уже зарегистрированных юзеров старый снимок
обновится только когда они снова залогинятся. Этот скрипт форсит обновление для тех,
у кого есть живой `vk_access_token`, не дожидаясь их входа: свежие друзья + аватары
сразу попадают в бёрздей-радар и possible_friends.

Ограничение: сохранённый токен может быть протухшим (VK вернёт ошибку) — такие юзеры
просто пропускаются и считаются в `failed`, скрипт на них не падает. Это заодно замер,
у скольких токен ещё жив.

Запуск на сервере: `python scripts/backfill_vk_friends.py [--dry-run]`.
"""

import time
from collections.abc import Callable

from sqlalchemy import select

from app.db import SessionLocal, User
from app.logging import logger
from app.vk import get_vk_user_friends

# Лёгкая пауза между запросами к VK, секунды: ~3 запроса/с (лимит VK на токен),
# чтобы массовый прогон не выглядел флудом с одного IP и VK не прикрыл доступ.
REQUEST_DELAY_SECONDS = 0.34


def backfill_user_vk_friends(
    user: User,
    fetch_friends: Callable[[str], list],
    *,
    dry_run: bool = False,
) -> bool:
    """Пересобрать `vk_friends_data` одного юзера свежими данными VK.

    Возвращает True при успешном обновлении, False при сбое VK-запроса (протухший
    токен, сеть). `fetch_friends` инжектится ради тестируемости без моков.
    """
    # vk_access_token не None — гарантировано фильтром выборки в main().
    assert user.vk_access_token is not None
    try:
        friends = fetch_friends(user.vk_access_token)
    except Exception as exc:
        logger.warning(
            'VK-друзья не обновлены (токен протух?): user={user_id}: {exc}',
            user_id=user.id,
            exc=exc,
        )
        return False
    if dry_run:
        logger.info(
            '[dry-run] обновил бы VK-друзей: user={user_id}, друзей={n}',
            user_id=user.id,
            n=len(friends),
        )
        return True
    user.vk_friends_data = friends
    logger.info(
        'Снимок VK-друзей обновлён: user={user_id}, друзей={n}',
        user_id=user.id,
        n=len(friends),
    )
    return True


def main(
    fetch_friends: Callable[[str], list] = get_vk_user_friends,
    *,
    dry_run: bool = False,
    delay_seconds: float = REQUEST_DELAY_SECONDS,
) -> None:
    logger.info(
        'Бэкфилл снимков VK-друзей запущен{suffix}',
        suffix=' (DRY-RUN, без записи)' if dry_run else '',
    )
    updated = 0
    failed = 0
    with SessionLocal() as db:
        users = db.scalars(select(User).where(User.vk_access_token.is_not(None))).all()
        logger.info('К обновлению: {n} юзеров с VK-токеном', n=len(users))
        for user in users:
            if backfill_user_vk_friends(user, fetch_friends, dry_run=dry_run):
                updated += 1
            else:
                failed += 1
            if not dry_run:
                db.add(user)
            # Пауза после каждого VK-запроса, чтобы не словить бан за флуд.
            time.sleep(delay_seconds)
        if not dry_run:
            db.commit()
    logger.info(
        'Бэкфилл завершён{suffix}: обновлено={updated}, токен мёртв/сбой={failed}',
        suffix=' (dry-run, ничего не записано)' if dry_run else '',
        updated=updated,
        failed=failed,
    )


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='Пересобрать снимок VK-друзей (с фото) у юзеров с живым токеном.'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Только показать, что было бы сделано, без записи в БД.',
    )
    main(dry_run=parser.parse_args().dry_run)
