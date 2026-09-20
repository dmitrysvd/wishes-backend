from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from starlette.status import HTTP_404_NOT_FOUND

from app.dependencies import PUSHES_TAG, get_db
from app.price_alerts import register_push_open
from app.schemas import PushOpenedSchema

router = APIRouter(tags=[PUSHES_TAG])

_OPENED_DESCRIPTION = """\
Сообщить, что приложение открылось по пушу — источник CTR по типу триггера
(фича 0013). Единственная клиентская работа фичи.

**Без `Authorization`.** На холодном старте обработчик пуша срабатывает до
восстановления auth-токена, поэтому ручка публичная: авторизацией служит сам
`delivery_id` — непредсказуемый UUID строки лога отправки, который бэк кладёт в
`data.delivery_id` пуша. Клиент шлёт его сразу при открытии по пушу, не
откладывая до логина. **Условие вызова — наличие `data.delivery_id`** в
открытом пуше, а не `type == price_alert`: `delivery_id` есть у **каждого**
пуша бэка (ДР, подписки, новые хотелки, сезонные, резерв, склад), клиент по
виду пуша ничего не различает. Вызов fire-and-forget: навигацию по `link` не
задерживать, ответа не ждать.

**Что такое «открытие».** Любое действие юзера по пушу: запуск из шторки
(холодный старт или из фона) и тап по тосту в foreground — один и тот же
`delivery_id`. Один best-effort вызов: ошибка сети — молча, без ретраев и без
накопления «непосланных». Хотелка к моменту тапа удалена или в архиве —
открытие всё равно шлите: оно состоялось, карточка покажет своё состояние.
Автоматический показ пуша открытием не считается.

Идемпотентно: первый вызов фиксирует время открытия, повторные (второй тап,
ретрай) — тоже `200`, второе открытие не считается.

Payload пуша по складу — `x-push-payload` этой операции (Swagger UI расширений
не показывает — читайте спек). Когда и кому пуш уходит — внутреннее поведение
бэка, в контракт не входит (intent 0013, `app/price_alerts.py`).
"""

# Payload пуша по складу: структурно, для аудитора и кодгена фронта
# (PROTOCOL.md §7). Формат как у остальных пушей: `notification` рисует ОС,
# `data` — для роутинга и тоста в foreground (`title`/`body` дублируют
# notification).
_PUSH_PAYLOAD = {
    'kind': 'price_alert',
    'notification': {
        'title': '„Кроссовки для бега“ подешевела',
        'body': '2 700 ₽ вместо 3 000 ₽',
    },
    'data': {
        'click_action': 'FLUTTER_NOTIFICATION_CLICK',
        'type': 'price_alert',
        'delivery_id': '5c1c9a2e-7b1d-4e3a-9f0a-2d6b8c4e1a77',
        'trigger': 'price',
        'link': 'https://hotelki.pro/wish?wishId=9b2d5e4a-1c3f-4a2b-8d6e-0f1a2b3c4d5e',
        'title': '„Кроссовки для бега“ подешевела',
        'body': '2 700 ₽ вместо 3 000 ₽',
    },
    'fields': {
        'type': 'Вид пуша, сейчас `price_alert`; клиенту для ручки не нужен.',
        'trigger': (
            '`price` (подешевело) | `availability` (снова в наличии) | `mixed` '
            '(дайджест с разными триггерами). Бэк знает его по доставке; в data — '
            'для отладки.'
        ),
        'link': (
            'Тот же ключ, что у остальных пушей, открывать через роутер: одна '
            'хотелка → `{FRONTEND_URL}/wish?wishId=<uuid>`; несколько → '
            '`{FRONTEND_URL}/` — свой список авторизованного юзера (S2).'
        ),
        'delivery_id': 'UUID строки лога отправки — тело `POST /push/opened`.',
    },
    'texts': {
        'one_price': {
            'title': '„Название“ подешевела',
            'body': '2 700 ₽ вместо 3 000 ₽',
        },
        'one_availability': {
            'title': '„Название“ снова в наличии',
            'body': '2 700 ₽ на WB',
        },
        'many': {
            'title': '3 вещи из списка подешевели или вернулись в наличие',
            'body': '„Кроссовки для бега“: 2 700 ₽ вместо 3 000 ₽',
        },
        'rules': (
            'Название в тексте — не длиннее 40 символов, хвост — многоточие; цены — '
            'целые рубли с пробелом-разделителем тысяч.'
        ),
    },
}


@router.post(
    '/push/opened',
    response_class=Response,
    status_code=200,
    description=_OPENED_DESCRIPTION,
    # Публичная ручка: bearer'а на холодном старте ещё нет — снимаем глобальное
    # требование ApiKey.
    openapi_extra={
        'security': [],
        'x-push-payload': _PUSH_PAYLOAD,
    },
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
    if not register_push_open(db, body.delivery_id):
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail='Not Found')
    return Response()
