from collections.abc import Iterable

from app.constants import HIDDEN_RESERVER_ID
from app.db import User, Wish
from app.schemas import WishReadSchema


def build_wish_read(wish: Wish, viewer: User) -> WishReadSchema:
    """Хотелка глазами зрителя: личность чужого дарителя скрыта заглушкой.

    Единственная точка сборки `WishReadSchema` из модели — роуты не возвращают
    `Wish` напрямую, иначе `reserved_by_id` уйдёт наружу как есть (владельцу это
    спойлер сюрприза, остальным — чужая PII). Свой резерв отдаётся как есть: по
    нему клиент показывает «зарезервировано мной» и даёт отмену. Состояние резерва
    при маскировке не теряется — оно в `is_reserved`.
    """
    schema = WishReadSchema.model_validate(wish, from_attributes=True)
    if wish.reserved_by_id is not None and wish.reserved_by_id != viewer.id:
        schema = schema.model_copy(update={'reserved_by_id': HIDDEN_RESERVER_ID})
    return schema


def build_wish_reads(wishes: Iterable[Wish], viewer: User) -> list[WishReadSchema]:
    return [build_wish_read(wish, viewer) for wish in wishes]
