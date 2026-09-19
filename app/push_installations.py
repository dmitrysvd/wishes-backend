"""Установки приложения как адресаты пушей (фича 0016): upsert из ручки и
разовый перенос адресов из `user.firebase_push_token`."""

from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import PushInstallation, User
from app.firebase import AddressCheck, check_address
from app.logging import logger
from app.utils import utc_now


def upsert_push_installation(
    db: Session, user: User, *, fid: str | None, push_token: str
) -> PushInstallation:
    """Найти установку по `fid`, иначе по `push_token`, иначе создать.

    Поиск глобальный: установка одна на устройство, и при логине другого
    аккаунта на нём переезжает к нему (иначе устройство B получает пуши A).
    Присланные адреса перезаписывают сохранённые: токен — всегда, FID — если
    прислан (старый клиент без FID не стирает FID, дописанный новым).
    Не коммитит — это дело вызывающего.
    """
    installation = None
    if fid is not None:
        installation = db.scalar(
            select(PushInstallation).where(PushInstallation.fid == fid)
        )
    if installation is None:
        installation = db.scalar(
            select(PushInstallation).where(PushInstallation.push_token == push_token)
        )
    if installation is None:
        installation = PushInstallation(user_id=user.id)
        db.add(installation)
    elif installation.user_id != user.id:
        logger.info(
            'Установка {address} переезжает к юзеру {user_id}',
            address=installation.address,
            user_id=user.id,
        )
        installation.user_id = user.id
    # Токен уникален: если он числится за другой строкой (старый клиент без
    # FID уже завёл её), она стала бы дублем той же установки — снимаем до
    # записи токена, иначе упрётся в UNIQUE.
    stale = db.scalar(
        select(PushInstallation).where(
            PushInstallation.push_token == push_token,
            PushInstallation.id != installation.id,
        )
    )
    if stale is not None:
        db.delete(stale)
        db.flush()
    if fid is not None:
        installation.fid = fid
    installation.push_token = push_token
    installation.saved_at = utc_now()
    db.flush()
    return installation


@dataclass
class MigrationReport:
    """Итог переноса адресов: по одному списку id юзеров на исход."""

    with_fid: list[UUID] = field(default_factory=list)
    token_only: list[UUID] = field(default_factory=list)
    dead: list[UUID] = field(default_factory=list)
    # dry-run не ответил про адрес (лимиты FCM и т.п.) — для повторного прогона.
    unknown: list[UUID] = field(default_factory=list)


def fid_candidate(push_token: str) -> str | None:
    """Кандидат-FID из токена: префикс до `:`. Формат недокументирован — это
    только гипотеза, её подтверждает dry-run в FCM."""
    prefix, sep, _ = push_token.partition(':')
    return prefix if sep and prefix else None


def migrate_legacy_tokens(
    db: Session,
    *,
    check: Callable[..., AddressCheck] = check_address,
    dry_run: bool = False,
) -> MigrationReport:
    """Сделать из `user.firebase_push_token` первую установку юзера.

    Идемпотентно: юзер с установками не трогается. Для каждого адреса —
    dry-run в FCM: FID подтверждён → установка с FID; иначе токен
    подтверждён → установка на токене; токен мёртв → установку не создаём;
    FCM не ответил про адрес → пропуск в отчёт. Старую колонку не трогаем
    (снимок для отката). `dry_run` — только отчёт, без записи.
    """
    report = MigrationReport()
    users = db.scalars(
        select(User).where(
            User.firebase_push_token.is_not(None), ~User.can_receive_push
        )
    ).all()
    for user in users:
        token = user.firebase_push_token
        assert token is not None
        fid = fid_candidate(token)
        fid_check = check(fid=fid) if fid else AddressCheck.dead
        if fid_check == AddressCheck.unknown:
            # Про FID ничего не ясно — не решаем за него, повторный прогон.
            report.unknown.append(user.id)
            continue
        if fid_check == AddressCheck.ok:
            report.with_fid.append(user.id)
        else:
            fid = None
            token_check = check(token=token)
            if token_check == AddressCheck.dead:
                report.dead.append(user.id)
                continue
            if token_check == AddressCheck.unknown:
                report.unknown.append(user.id)
                continue
            report.token_only.append(user.id)
        if not dry_run:
            db.add(
                PushInstallation(
                    user_id=user.id,
                    fid=fid,
                    push_token=token,
                    saved_at=user.firebase_push_token_saved_at or utc_now(),
                )
            )
    if not dry_run:
        db.commit()
    return report
