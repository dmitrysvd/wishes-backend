from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

import httpx
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
from app.constants import ACTIVITY_STATE_USER_ID, STORE_REQUEST_TIMEOUT_SECONDS
from app.db import SessionLocal, User, Wish
from app.helpers.browser_transport import BrowserTransport

# Реэкспорт: роутеры берут параметры пагинации из dependencies, как и остальные.
from app.helpers.pagination import PaginationParams as PaginationParams

# Теги для OpenAPI документации
AUTH_TAG = 'auth'
WISHES_TAG = 'wishes'
USERS_TAG = 'users'
PUBLIC_TAG = 'public'
DEV_TAG = 'dev'


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
    user = _authenticate(request, db)
    # Метка для прибора возврата: кто сделал запрос. Саму запись делает мидлварь
    # после ответа (app.main.track_user_activity) — здесь только помечаем, чтобы
    # аутентификация оставалась чистой и не лезла в транзакцию запроса.
    setattr(request.state, ACTIVITY_STATE_USER_ID, user.id)
    return user


def _authenticate(request: Request, db: Session) -> User:
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


def get_store_client() -> Iterator[httpx.Client]:
    """HTTP-клиент для свежего запроса цены к магазину (фича 0011).

    Зависимость, а не глобальный объект: тесты подменяют её клиентом на
    `httpx.MockTransport` через `app.dependency_overrides` — без моков внутри
    логики. С отпечатком обычного httpx WB отвечает 403 — см. BrowserTransport.
    Таймаут — бюджет, обещанный контрактом (не дольше 10 с).
    """
    with httpx.Client(
        transport=BrowserTransport(timeout=STORE_REQUEST_TIMEOUT_SECONDS)
    ) as client:
        yield client
