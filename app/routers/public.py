from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.status import HTTP_404_NOT_FOUND

from app.db import User, Wish
from app.dependencies import PUBLIC_TAG, get_db
from app.schemas import PublicUserSchema, PublicWishlistSchema, PublicWishSchema

router = APIRouter(tags=[PUBLIC_TAG], prefix='/public')


@router.get('/users/{user_id}/wishlist', response_model=PublicWishlistSchema)
def public_wishlist(user_id: UUID, db: Session = Depends(get_db)):
    """Публичный вишлист для веб-страницы — открывается без авторизации.

    Отдаёт только безопасные данные: имя/фото владельца и его активные
    хотелки с флагом, зарезервирована ли каждая (без личности дарителя).
    """
    user = db.scalars(select(User).where(User.id == user_id)).one_or_none()
    if not user:
        raise HTTPException(HTTP_404_NOT_FOUND, 'Пользователь не найден')
    wishes = db.scalars(Wish.get_active_wish_query().where(Wish.user == user)).all()
    return PublicWishlistSchema(
        user=PublicUserSchema.model_validate(user),
        wishes=[PublicWishSchema.model_validate(wish) for wish in wishes],
    )
