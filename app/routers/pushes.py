from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from starlette.status import HTTP_404_NOT_FOUND, HTTP_501_NOT_IMPLEMENTED

from app.dependencies import PUSHES_TAG, get_db
from app.schemas import PushOpenedSchema

router = APIRouter(tags=[PUSHES_TAG])

_OPENED_DESCRIPTION = """\
Сообщить, что приложение открылось по пушу — источник CTR по типу триггера
(фича 0013). Единственная клиентская работа фичи.

**Без `Authorization`.** На холодном старте обработчик пуша срабатывает до
восстановления auth-токена, поэтому ручка публичная: авторизацией служит сам
`delivery_id` — непредсказуемый UUID строки лога отправки, который бэк кладёт в
`data.delivery_id` пуша. Клиент шлёт его сразу при открытии по пушу, не
откладывая до логина.

**Что лежит в `data` пуша по складу** (вид `price_alert`):
`link` — deep link: одна хотелка → `{FRONTEND_URL}/wish?wishId=<uuid>`,
несколько → `{FRONTEND_URL}/`; `trigger` — `price` (подешевело) |
`availability` (снова в наличии) | `mixed` (дайджест с обоими типами);
`delivery_id` — для этой ручки. Клиент открывает `link` и шлёт `delivery_id`;
`trigger` ему не нужен — бэк знает его по доставке.

Идемпотентно: первый вызов фиксирует время открытия, повторные (второй тап,
ретрай сети) — тоже `200`, второе открытие не считается. Пуши других видов
(ДР, резерв, …) `delivery_id` пока не несут — по ним ручку не зовите.
"""


@router.post(
    '/push/opened',
    response_class=Response,
    status_code=200,
    description=_OPENED_DESCRIPTION,
    # Публичная ручка: bearer'а на холодном старте ещё нет — снимаем глобальное
    # требование ApiKey.
    openapi_extra={'security': []},
    responses={
        200: {
            'description': (
                'Открытие учтено (или уже было учтено раньше — повтор безвреден). '
                'Тело пустое: клиенту читать нечего.'
            )
        },
        HTTP_404_NOT_FOUND: {
            'description': (
                'Неизвестный `delivery_id`: такой доставки нет (метка испорчена, '
                'либо лог отправок уже почищен). Ничего не записано; клиент '
                'молчит и не ретраит — на UX не влияет.'
            ),
            'content': {'application/json': {'example': {'detail': 'Not Found'}}},
        },
        422: {
            'description': (
                '`delivery_id` — не UUID либо тело пустое. Ничего не записано.'
            )
        },
    },
)
def push_opened(
    body: PushOpenedSchema,
    db: Session = Depends(get_db),
) -> Response:
    """Открытие по пушу — см. `_OPENED_DESCRIPTION`."""
    raise HTTPException(status_code=HTTP_501_NOT_IMPLEMENTED)
