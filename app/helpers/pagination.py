from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.dependencies import PaginationParams
from app.schemas import ItemT, PageSchema


def paginate(
    db: Session,
    query: Select[tuple[Any]],
    params: PaginationParams,
    item_schema: type[ItemT],
) -> PageSchema[ItemT]:
    """Выполнить offset/limit-пагинацию запроса и собрать страницу-ответ."""
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.limit(params.limit).offset(params.offset)).all()
    items = [item_schema.model_validate(row) for row in rows]
    return PageSchema(
        items=items,
        total=total,
        has_next=params.offset + len(items) < total,
        has_previous=params.offset > 0,
    )
