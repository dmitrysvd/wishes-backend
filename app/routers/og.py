from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import User, Wish
from app.dependencies import get_db
from app.helpers.og_helpers import build_og_context

TEMPLATES_DIR = Path(__file__).parent.parent / 'templates'
templates = Jinja2Templates(directory=TEMPLATES_DIR)

router = APIRouter()


def _parse_user_id(raw: str | None) -> UUID | None:
    """userId из deep link. Кривой/пустой → None: краулеру отдаём бренд-фолбэк,
    а не 422 (иначе ссылка в чате выглядит мёртвой)."""
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


@router.get('/og/user', include_in_schema=False, response_class=HTMLResponse)
def og_user(
    request: Request,
    userId: str | None = None,  # noqa: N803 — имя параметра задано deep link'ом
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Серверный HTML с Open Graph-тегами для расшаренной ссылки на вишлист.

    Не входит в OpenAPI-контракт (`include_in_schema=False`): потребитель —
    краулеры соцсетей, не Flutter-фронт; отдаём HTML, а не JSON. nginx направляет
    сюда запросы `/user` с UA краулера; живые юзеры идут в SPA (см. deploy/nginx).
    """
    user: User | None = None
    wish_count = 0
    user_id = _parse_user_id(userId)
    if user_id is not None:
        user = db.scalars(select(User).where(User.id == user_id)).one_or_none()
    if user is not None:
        active_wishes = Wish.get_active_wish_query().where(Wish.user_id == user.id)
        wish_count = (
            db.scalar(select(func.count()).select_from(active_wishes.subquery())) or 0
        )
    context = build_og_context(user, wish_count)
    return templates.TemplateResponse(request, 'og_user.html', context)
