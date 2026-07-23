from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Query, Request
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
from app.constants import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT
from app.db import SessionLocal, User, Wish

# Теги для OpenAPI документации
AUTH_TAG = 'auth'
WISHES_TAG = 'wishes'
USERS_TAG = 'users'
PUBLIC_TAG = 'public'
DEV_TAG = 'dev'


class PaginationParams:
    """Общие query-параметры пагинации для списочных эндпоинтов."""

    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        self.limit = limit
        self.offset = offset


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _is_test_auth_token(token: str) -> bool:
    """Похож ли заголовок на токен dev/test-байпаса (фича 0009).

    Активно только если секрет сконфигурен в окружении. Формат — `{secret}:{id}`;
    двоеточие обязательно, чтобы токен не совпал по одному лишь префиксу.
    """
    return settings.TEST_AUTH_SECRET is not None and token.startswith(
        f'{settings.TEST_AUTH_SECRET}:'
    )


def _resolve_test_auth_user(token: str, db: Session) -> User:
    """Достать сид-юзера по токену dev/test-байпаса.

    Принимаем ТОЛЬКО сид-юзеров (`is_test`): даже с валидным секретом токен на
    реальный аккаунт не резолвится. Битый UUID/несуществующий/не-тест → 401 без
    утечки, работают ли какие-то юзеры.
    """
    raw_id = token.split(':', 1)[1]
    try:
        user_id = UUID(raw_id)
    except ValueError:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED, detail='Not authenticated'
        ) from None
    user = db.execute(
        select(User).where(User.id == user_id, User.is_test.is_(True))
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED, detail='Not authenticated'
        )
    return user


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.headers.get('Authorization')
    if not token:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED, detail='Not authenticated'
        )

    # dev/test-байпас (фича 0009): работает и в проде-подобной среде, но только
    # для сид-юзеров. Гейтится наличием секрета, не `IS_DEBUG`.
    if _is_test_auth_token(token):
        return _resolve_test_auth_user(token, db)

    try:
        decoded_token = verify_id_token(token)
    except ExpiredIdTokenError:
        raise HTTPException(HTTP_401_UNAUTHORIZED, 'Token expired') from None
    except InvalidIdTokenError:
        raise HTTPException(HTTP_401_UNAUTHORIZED, 'Invalid token') from None
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
