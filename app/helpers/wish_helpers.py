from uuid import UUID

from pydantic import HttpUrl

from app.constants import HIDDEN_RESERVER_ID
from app.db import Wish
from app.schemas import OtherUserSchema, WishReadSchema


def _visible_reserver_id(wish: Wish, viewer_id: UUID) -> UUID | None:
    """`reserved_by_id` глазами зрителя: свой резерв — как есть, чужой — заглушкой.

    Реальный чужой id не отдаём никому: владельцу списка это спойлер сюрприза,
    прочим — чужая PII. Но и `null` вместо него нельзя — уже зашипленные клиенты
    считают непустое поле признаком резерва и показали бы чужой резерв свободным,
    поэтому чужая резервация схлопывается в `HIDDEN_RESERVER_ID`.
    """
    if wish.reserved_by_id is None:
        return None
    if wish.reserved_by_id == viewer_id:
        return wish.reserved_by_id
    return HIDDEN_RESERVER_ID


def build_wish_read(wish: Wish, viewer_id: UUID) -> WishReadSchema:
    """Собрать хотелку под конкретного зрителя, скрыв личность чужого дарителя.

    Единая точка сборки `WishReadSchema` из модели: маскировка не должна зависеть
    от того, какой роут отдаёт хотелку. Состояние резерва при этом не теряется —
    оно в `is_reserved` (и в непустом `reserved_by_id`, см. `_visible_reserver_id`).
    """
    return WishReadSchema(
        id=wish.id,
        name=wish.name,
        description=wish.description,
        price=int(wish.price) if wish.price is not None else None,
        link=HttpUrl(wish.link) if wish.link else None,
        is_archived=wish.is_archived,
        is_reserved=wish.is_reserved,
        reserved_by_id=_visible_reserver_id(wish, viewer_id),
        image=wish.image,  # type: ignore[arg-type]  # имя файла → URL, см. валидатор
        recommendation_id=wish.recommendation_id,
        user=OtherUserSchema.model_validate(wish.user),
    )
