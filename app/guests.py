"""Гостевой резерв на публичной странице (фича 0018).

Гость узнаётся по куке (`Guest.token`), создаётся первым успешным резервом.
Резерв гостем — `Wish.reserved_by_guest_id`; для всех, кроме этого гостя, он
неотличим от обычного. При входе в том же браузере `merge_guest` переносит
резервы на аккаунт и подписывает аккаунт на владельцев. Контракт — операции
`/public/users/{user_id}/wishes/{wish_id}/…` и поля слияния в ответах auth.
"""

import enum
import math
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy import delete, exists, func, insert, select
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import FollowAction, FollowEventSource
from app.db import (
    FollowEvent,
    Guest,
    GuestReservationEvent,
    User,
    Wish,
    user_following_table,
)
from app.logging import logger
from app.utils import utc_now

# Гость без резервов живёт сутки: чистка не должна отнять куку у гостя, который
# только что снял резерв и сейчас бронирует другую хотелку.
ORPHAN_GUEST_GRACE = timedelta(days=1)


class GuestOutcome(enum.Enum):
    """Исход гостевого действия; роутер переводит его в HTTP-статус."""

    ok = 'ok'
    owner_not_found = 'owner_not_found'  # 404
    wish_gone = 'wish_gone'  # 410
    reserved_by_other = 'reserved_by_other'  # 409 на резерве
    not_yours = 'not_yours'  # 403 на снятии
    limited = 'limited'  # 429


@dataclass(frozen=True)
class GuestResult:
    outcome: GuestOutcome
    wish: Wish | None = None
    # Гость, созданный этим запросом: роутер ставит его куку.
    new_guest: Guest | None = None


@dataclass
class MergeResult:
    merged_reservations: int = 0
    followed_owner_ids: list[UUID] = field(default_factory=list)


def client_ip(request: Request) -> str:
    """IP клиента для лимита. За нашим nginx — последний адрес `X-Forwarded-For`:
    его дописывает сам nginx (`$proxy_add_x_forwarded_for`), а всё левее прислал
    клиент и подделывается. Без прокси (стенд) — адрес сокета."""
    forwarded = request.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[-1].strip()
    return request.client.host if request.client else 'unknown'


def find_guest(db: Session, token: str | None) -> Guest | None:
    """Гость по куке. Слитый в аккаунт гость — уже не гость: его кука устарела."""
    if not token:
        return None
    return db.scalar(
        select(Guest).where(Guest.token == token, Guest.merged_user_id.is_(None))
    )


def is_reserved_by(wish: Wish, guest: Guest | None) -> bool:
    return guest is not None and wish.reserved_by_guest_id == guest.id


def _locked_wish(db: Session, owner: User, wish_id: UUID) -> Wish | None:
    """Активная хотелка владельца под блокировкой строки: два одновременных тапа
    не займут её оба — второй дождётся первого и увидит резерв."""
    return db.scalar(
        Wish.get_active_wish_query()
        .where(Wish.id == wish_id, Wish.user_id == owner.id)
        .with_for_update()
    )


def _count(db: Session, *conditions: Any, model: type = Wish) -> int:
    return db.execute(
        select(func.count()).select_from(model).where(*conditions)
    ).scalar_one()


def _over_limit(db: Session, owner: User, guest: Guest | None, ip: str) -> bool:
    """Три слоя защиты — наружу один исход, какой сработал, не раскрываем."""
    if guest is not None:
        held_in_list = _count(
            db, Wish.user_id == owner.id, Wish.reserved_by_guest_id == guest.id
        )
        if held_in_list >= settings.GUEST_RESERVE_PER_GUEST_PER_LIST:
            logger.info('Лимит гостя в списке: guest={g}', g=guest.id)
            return True
    active_wishes = _count(db, Wish.user_id == owner.id, ~Wish.is_archived)
    list_cap = max(
        settings.GUEST_RESERVE_LIST_MIN,
        math.floor(active_wishes * settings.GUEST_RESERVE_LIST_SHARE),
    )
    held_by_guests = _count(
        db,
        Wish.user_id == owner.id,
        ~Wish.is_archived,
        Wish.reserved_by_guest_id.is_not(None),
    )
    if held_by_guests >= list_cap:
        logger.info('Лимит гостей на список: owner={o}', o=owner.id)
        return True
    recent_from_ip = _count(
        db,
        GuestReservationEvent.ip == ip,
        GuestReservationEvent.created_at > utc_now() - timedelta(minutes=1),
        model=GuestReservationEvent,
    )
    if recent_from_ip >= settings.GUEST_RESERVE_PER_IP_PER_MINUTE:
        logger.warning('Лимит гостевых резервов с IP: ip={ip}', ip=ip)
        return True
    return False


def guest_reserve(
    db: Session, owner_id: UUID, wish_id: UUID, token: str | None, ip: str
) -> GuestResult:
    owner = db.get(User, owner_id)
    if owner is None:
        return GuestResult(GuestOutcome.owner_not_found)
    wish = _locked_wish(db, owner, wish_id)
    if wish is None:
        return GuestResult(GuestOutcome.wish_gone)
    guest = find_guest(db, token)
    if is_reserved_by(wish, guest):
        # Повторный тап по своей — не ошибка и не новый резерв (лимиты не тратит).
        return GuestResult(GuestOutcome.ok, wish)
    if wish.is_reserved:
        return GuestResult(GuestOutcome.reserved_by_other, wish)
    if _over_limit(db, owner, guest, ip):
        return GuestResult(GuestOutcome.limited, wish)
    new_guest = None
    if guest is None:
        guest = new_guest = Guest(token=secrets.token_urlsafe(32))
        db.add(guest)
        db.flush()
    wish.reserved_by_guest_id = guest.id
    # Как у обычного резерва: по этому моменту крон шлёт владельцу пуш «резерв»,
    # он же нужен аналитике.
    wish.reserved_at = utc_now()
    db.add(GuestReservationEvent(guest_id=guest.id, wish_id=wish.id, ip=ip))
    db.commit()
    return GuestResult(GuestOutcome.ok, wish, new_guest)


def guest_cancel(
    db: Session, owner_id: UUID, wish_id: UUID, token: str | None
) -> GuestResult:
    owner = db.get(User, owner_id)
    if owner is None:
        return GuestResult(GuestOutcome.owner_not_found)
    wish = _locked_wish(db, owner, wish_id)
    if wish is None:
        return GuestResult(GuestOutcome.wish_gone)
    guest = find_guest(db, token)
    if is_reserved_by(wish, guest):
        wish.reserved_by_guest_id = None
        wish.reserved_at = None
        db.commit()
        return GuestResult(GuestOutcome.ok, wish)
    if wish.is_reserved:
        return GuestResult(GuestOutcome.not_yours, wish)
    # Уже свободна — снимать нечего, повтор идемпотентен.
    return GuestResult(GuestOutcome.ok, wish)


def merge_guest(db: Session, user: User, token: str | None) -> MergeResult:
    """Перенести резервы гостя из этого браузера на аккаунт `user`.

    Одна транзакция: либо перенесено всё и гость помечен слитым, либо ничего —
    упавшее слияние роняет вход, кука остаётся, следующий вход повторит.
    Переносятся резервы на все неудалённые хотелки, включая архивные (архивация
    резерв не снимает). Резервы на хотелки самого `user` снимаются: резервировать
    своё нельзя. На владельца каждого списка с перенесённым резервом `user`
    подписывается (если ещё не подписан) — без пуша «новый подписчик»: по его
    времени владелец угадал бы дарителя.
    """
    guest = find_guest(db, token)
    if guest is None:
        return MergeResult()
    wishes = db.scalars(
        select(Wish)
        .where(Wish.reserved_by_guest_id == guest.id)
        .order_by(Wish.reserved_at, Wish.id)
        .with_for_update()
    ).all()
    result = MergeResult()
    owner_ids: list[UUID] = []
    for wish in wishes:
        wish.reserved_by_guest_id = None
        if wish.user_id == user.id:
            wish.reserved_at = None
            continue
        wish.reserved_by_id = user.id
        result.merged_reservations += 1
        if wish.user_id not in owner_ids:
            owner_ids.append(wish.user_id)
    already_followed = set(
        db.scalars(
            select(user_following_table.c.followed_id).where(
                user_following_table.c.follower_id == user.id,
                user_following_table.c.followed_id.in_(owner_ids),
            )
        )
    )
    for owner_id in owner_ids:
        if owner_id in already_followed:
            continue
        db.execute(
            insert(user_following_table).values(
                follower_id=user.id, followed_id=owner_id
            )
        )
        db.add(
            FollowEvent(
                actor_id=user.id,
                target_id=owner_id,
                action=FollowAction.follow,
                source=FollowEventSource.guest_reservation,
                # Приватность резерва: владелец не получает «новый подписчик».
                is_notification_sent=True,
            )
        )
        result.followed_owner_ids.append(owner_id)
    guest.merged_user_id = user.id
    guest.merged_at = utc_now()
    db.commit()
    # Рёбра вставлены мимо relationship — сбрасываем закэшированные списки.
    db.expire(user)
    logger.info(
        'Гость {guest} слит в {user}: резервов {n}, подписок {f}',
        guest=guest.id,
        user=user.id,
        n=result.merged_reservations,
        f=len(result.followed_owner_ids),
    )
    return result


def delete_orphan_guests(db: Session, now: datetime) -> int:
    """Удалить несливавшихся гостей без единого резерва старше суток.

    Слитые остаются — это данные конверсии гость → аккаунт.
    """
    orphan_ids = list(
        db.scalars(
            select(Guest.id).where(
                Guest.merged_user_id.is_(None),
                Guest.created_at < now - ORPHAN_GUEST_GRACE,
                ~exists().where(Wish.reserved_by_guest_id == Guest.id),
            )
        )
    )
    if orphan_ids:
        db.execute(delete(Guest).where(Guest.id.in_(orphan_ids)))
        db.commit()
    return len(orphan_ids)
