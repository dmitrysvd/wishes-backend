from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from firebase_admin.auth import (
    ExpiredIdTokenError,
    InvalidIdTokenError,
    verify_id_token,
)
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.status import (
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)

from app.config import settings
from app.db import SessionLocal, User, Wish

# Теги для OpenAPI документации
AUTH_TAG = 'auth'
WISHES_TAG = 'wishes'
USERS_TAG = 'users'


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.headers.get('Authorization')
    if not token:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED, detail='Not authenticated'
        )

    if settings.IS_DEBUG and token.startswith(settings.TEST_TOKEN):
        user_id = UUID(token.split(':')[-1])
        user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED, detail='Not authenticated'
            )
        return user

    try:
        decoded_token = verify_id_token(token)
    except ExpiredIdTokenError:
        raise HTTPException(HTTP_401_UNAUTHORIZED, 'Token expired')
    except InvalidIdTokenError:
        raise HTTPException(HTTP_401_UNAUTHORIZED, 'Invalid token')
    uid = decoded_token['uid']
    user = db.execute(select(User).where(User.firebase_uid == uid)).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED, detail='Not authenticated'
        )
    return user


def get_current_user_wish(
    wish_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Wish:
    wish = db.scalars(select(Wish).where(Wish.id == wish_id)).one_or_none()
    if not wish:
        raise HTTPException(HTTP_404_NOT_FOUND)
    if wish.user != user:
        raise HTTPException(HTTP_403_FORBIDDEN)
    return wish
