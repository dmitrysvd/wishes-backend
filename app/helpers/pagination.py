from typing import Annotated, Any

from fastapi import Query
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.constants import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from app.schemas import ItemT, PageSchema


class PaginationParams:
    """Общие query-параметры пагинации для списочных эндпоинтов.

    Живёт рядом с `paginate`, а не в `app.dependencies`: иначе helpers зависят от
    dependencies, а dependencies — от helpers (цикл импортов).
    """

    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        self.limit = limit
        self.offset = offset


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
