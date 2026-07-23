from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from starlette.status import HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND

from app.config import settings
from app.dependencies import DEV_TAG, get_db
from app.schemas import TestTokenRequestSchema, TestTokenResponseSchema
from app.test_auth import build_test_token, get_or_create_test_user

router = APIRouter(tags=[DEV_TAG])

_TEST_TOKEN_RESPONSES: dict[int | str, dict[str, Any]] = {
    403: {
        'description': (
            'Неверный или отсутствующий секрет. Ответ не раскрывает, работают ли '
            'какие-то сид-юзеры.'
        ),
        'content': {'application/json': {'example': {'detail': 'Not authenticated'}}},
    },
    404: {
        'description': (
            'Байпас выключен: секрет не сконфигурен в окружении (прод). Эндпоинт '
            'ведёт себя так, будто его нет.'
        ),
        'content': {'application/json': {'example': {'detail': 'Not Found'}}},
    },
}


@router.post(
    '/dev/test_token',
    response_model=TestTokenResponseSchema,
    # Публичный вход в тест-сессию: bearer'а у клиента ещё нет — снимаем
    # глобальное требование ApiKey.
    openapi_extra={'security': []},
    responses=_TEST_TOKEN_RESPONSES,
)
def issue_test_token(
    request_data: TestTokenRequestSchema,
    db: Session = Depends(get_db),
) -> TestTokenResponseSchema:
    """Выдать bearer сид-юзера для авто-тестов без прохождения OAuth (фича 0009).

    Единственный путь получить авторизованную сессию в headless-тесте (веб-смок,
    integration_test, CI): headless-браузер не проходит живой Google/VK OAuth.
    Возвращает обычный bearer, эквивалентный токену после OAuth, — существующий
    клиент (`ApiRepository`, `login.sh`) работает без изменений.

    Безопасность by construction. Байпас выдаёт токен ТОЛЬКО детерминированным
    сид-юзерам (`is_test`) и НЕ принимает `user_id` — реальный аккаунт им не
    выдаётся, даже зная секрет. Включённость гейтится наличием `TEST_AUTH_SECRET`
    в окружении: в проде секрет не задан → эндпоинт отдаёт `404`. Секрет — не в
    репозитории (как ключи подписи), утечка секрета ≠ утечка данных прода.

    Сайд-эффект (get-or-create): первый вызов для персоны детерминированно
    создаёт сид-юзера (для `rich` — с VK-друзьями-с-ДР, подписками, желаниями и
    резервом); повторный — находит того же юзера и ничего не мутирует
    (идемпотентно). Сид-данные детерминированы; ДР друзей — фиксированные
    календарные даты, поэтому радар всегда непуст, а ассерты не плывут.
    """
    if settings.TEST_AUTH_SECRET is None:
        # Секрет не сконфигурен — байпаса не существует.
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail='Not Found')
    if request_data.secret != settings.TEST_AUTH_SECRET:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail='Not authenticated')

    user = get_or_create_test_user(db, request_data.persona)
    return TestTokenResponseSchema(
        token=build_test_token(user),
        persona=request_data.persona,
        user_id=user.id,
    )
