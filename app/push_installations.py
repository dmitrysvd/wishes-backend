"""Установки приложения как адресаты пушей (фича 0016): upsert из ручки."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import PushInstallation, User
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
