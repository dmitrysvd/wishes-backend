from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import HttpUrl
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.status import HTTP_404_NOT_FOUND

from app.db import User, Wish
from app.dependencies import PUBLIC_TAG, get_db
from app.schemas import (
    PublicBirthdaySchema,
    PublicOwnerSchema,
    PublicWishlistSchema,
    PublicWishSchema,
)

router = APIRouter(tags=[PUBLIC_TAG], prefix='/public')


def _build_owner(user: User) -> PublicOwnerSchema:
    """Собирает публичные данные владельца, отрезая PII (email/телефон/год ДР)."""
    birthday = None
    if user.birth_date:
        birthday = PublicBirthdaySchema(
            day=user.birth_date.day, month=user.birth_date.month
        )
    return PublicOwnerSchema(
        id=user.id,
        display_name=user.display_name,
        photo_url=HttpUrl(user.photo_url) if user.photo_url else None,
        birthday=birthday,
    )


def _build_wish(wish: Wish) -> PublicWishSchema:
    """Собирает публичную хотелку: путь к картинке и булев флаг резерва без личности."""
    return PublicWishSchema(
        id=wish.id,
        name=wish.name,
        description=wish.description,
        price=int(wish.price) if wish.price is not None else None,
        link=HttpUrl(wish.link) if wish.link else None,
        image_url=f'/media/wish_images/{wish.image}' if wish.image else None,
        is_reserved=wish.is_reserved,
    )


@router.get(
    '/users/{user_id}/wishlist',
    response_model=PublicWishlistSchema,
    summary='Публичный вишлист владельца',
    responses={
        200: {
            'description': (
                'Вишлист найден. `wishes` может быть пустым (у владельца нет '
                'активных хотелок) — это не ошибка, показывайте заглушку + CTA.'
            ),
        },
        404: {
            'description': (
                'Пользователь с таким `user_id` не найден или удалён. '
                'Показывайте страницу 404, а не падение.'
            ),
            'content': {
                'application/json': {'example': {'detail': 'Пользователь не найден'}}
            },
        },
    },
)
def public_wishlist(
    user_id: UUID, db: Session = Depends(get_db)
) -> PublicWishlistSchema:
    """Публичный вишлист для веб-страницы — открывается без авторизации и установки.

    Главный экран виральной петли: даритель-неюзер открывает расшаренную ссылку и
    сразу видит владельца и его активные желания, без требования установить приложение.

    **Что отдаётся:** владелец (имя, фото, день+месяц ДР — без года) и список активных
    хотелок; по каждой — булев `is_reserved` без личности дарителя.

    **Приватность (закон):** наружу не идут email, телефон и год рождения владельца;
    не раскрывается, кто зарезервировал; архивные хотелки исключены.

    **Состояния:** `200` со списком; `200` с пустым `wishes` (нет желаний); `404`
    (нет такого пользователя). В фазе 1 все списки публичны — приватного режима нет.
    """
    user = db.scalars(select(User).where(User.id == user_id)).one_or_none()
    if not user:
        raise HTTPException(HTTP_404_NOT_FOUND, 'Пользователь не найден')
    wishes = db.scalars(Wish.get_active_wish_query().where(Wish.user == user)).all()
    return PublicWishlistSchema(
        owner=_build_owner(user),
        wishes=[_build_wish(wish) for wish in wishes],
    )
