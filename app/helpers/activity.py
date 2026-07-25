"""Прибор возврата: суточный след активности юзера + метки для access-лога.

Два прибора с разными горизонтами, оба питаются из одной HTTP-мидлвари:

* `UserActivityDay` — компактный долгий горизонт (возврат, DAU/WAU/MAU,
  адопшен радара). Живёт в БД, переживает редеплой и ротацию логов; на нём
  меряется сезонный возврат, который у продукта событийный и годовой.
* Заголовки ответа `X-User-Id` / `X-Route` — детальное короткое окно. Их
  подхватывает nginx (он на хосте, его лог переживает редеплой контейнера) и
  кладёт в access-лог, где уже есть метод, статус и тайминг. Так весь
  серверный поток действий инструментируется без таксономии событий.
  Наружу клиенту заголовки не уходят — nginx их снимает `proxy_hide_header`.

Запись в БД идёт после ответа, в отдельной сессии, под общим try/except:
инструментация не имеет права ни уронить запрос, ни попасть в его транзакцию.
Стоимость — один upsert на запрос (не больше строки на юзера в сутки).
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from starlette.requests import Request
from starlette.responses import Response

from app.constants import ACTIVITY_STATE_RADAR_OPENED, ACTIVITY_STATE_USER_ID
from app.db import SessionLocal, UserActivityDay
from app.logging import logger
from app.utils import utc_now

# Заголовки-метки для access-лога nginx. Значения читаются как
# `$upstream_http_x_user_id` / `$upstream_http_x_route`.
USER_ID_HEADER = 'X-User-Id'
# Шаблон роута (`/users/{user_id}/wishes`), а НЕ сырой путь: в сыром пути каждый
# UUID даёт свой бакет, и лог перестаёт агрегироваться.
ROUTE_HEADER = 'X-Route'


def record_activity(
    user_id: UUID, radar_opens: int = 0, now: datetime | None = None
) -> None:
    """Отметить активность юзера за текущие сутки (upsert).

    `now` параметризован ради тестируемости без подмены системного времени.
    """
    now = now or utc_now()
    statement = (
        pg_insert(UserActivityDay)
        .values(
            user_id=user_id,
            activity_date=now.date(),
            first_seen_at=now,
            last_seen_at=now,
            request_count=1,
            radar_open_count=radar_opens,
        )
        .on_conflict_do_update(
            index_elements=['user_id', 'activity_date'],
            set_={
                # first_seen_at не трогаем — он про первый заход в эти сутки.
                'last_seen_at': now,
                'request_count': UserActivityDay.request_count + 1,
                'radar_open_count': UserActivityDay.radar_open_count + radar_opens,
            },
        )
    )
    with SessionLocal() as session:
        try:
            session.execute(statement)
            session.commit()
        except Exception:
            # Явный rollback, а не только закрытие сессии: иначе сбойная запись
            # оставила бы транзакцию в аборте. Ошибку отдаём наверх — там её
            # залогируют и проглотят.
            session.rollback()
            raise


def record_request_activity(request: Request) -> None:
    """Снять след с завершённого запроса. Best-effort: ничего не поднимает.

    Неавторизованные запросы (публичный вишлист, OG, health) следа не оставляют:
    юзера в них нет, а прибор меряет именно возврат конкретных людей.
    """
    user_id = getattr(request.state, ACTIVITY_STATE_USER_ID, None)
    if user_id is None:
        return
    radar_opens = 1 if getattr(request.state, ACTIVITY_STATE_RADAR_OPENED, False) else 0
    try:
        record_activity(user_id, radar_opens)
    except Exception as exc:
        logger.warning(
            'Не удалось записать след активности user_id={user_id}: {exc}',
            user_id=user_id,
            exc=exc,
        )


def set_activity_headers(request: Request, response: Response) -> None:
    """Проставить метки для access-лога nginx.

    `X-Route` ставится и для неавторизованных запросов (публичный вишлист, OG):
    их тоже полезно агрегировать по шаблону. `X-User-Id` — только когда юзер
    известен.
    """
    user_id = getattr(request.state, ACTIVITY_STATE_USER_ID, None)
    if user_id is not None:
        response.headers[USER_ID_HEADER] = str(user_id)
    # scope['route'] появляется после роутинга, т.е. уже доступен в мидлвари.
    route_path = getattr(request.scope.get('route'), 'path', None)
    if route_path:
        response.headers[ROUTE_HEADER] = route_path
