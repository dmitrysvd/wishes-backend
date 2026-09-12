import enum
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.admin.setup import setup_admin
from app.config import settings
from app.db import engine

# Реэкспорт для обратной совместимости
from app.dependencies import get_current_user, get_db
from app.helpers import (
    get_user_deep_link,
    record_request_activity,
    set_activity_headers,
)
from app.logging import logger
from app.routers import (
    auth,
    birthday_radar,
    dev,
    og,
    public,
    recommendations,
    users,
    wishes,
)

__all__ = ['app', 'get_db', 'get_current_user', 'get_user_deep_link']

BASE_DIR = Path(__file__).parent.parent
APP_DIR = BASE_DIR / 'app'
TEMPLATES_DIR = APP_DIR / 'templates'

settings.LOGS_DIR.mkdir(exist_ok=True, parents=True)


app = FastAPI(
    title='Хотелки',
    root_path=settings.URL_ROOT_PATH,
)
templates = Jinja2Templates(directory=TEMPLATES_DIR)

if settings.IS_DEBUG:
    app.mount('/static', StaticFiles(directory=BASE_DIR / 'static'), name='static')
    app.mount('/media', StaticFiles(directory=settings.MEDIA_ROOT), name='media')

# CORS ограничен явным списком origin-ов из настроек: при allow_credentials=True
# использовать allow_origins=['*'] небезопасно (любой сайт получает доступ с куками).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.middleware('http')
async def internal_exception_handler(request: Request, call_next):
    try:
        response = await call_next(request)
    except Exception as exc:
        if not settings.IS_DEBUG:
            # Уровень ERROR уходит в Hawk стоком loguru (app/hawk.py).
            logger.exception('Exception')
        raise exc
    return response


@app.middleware('http')
async def track_user_activity(request: Request, call_next):
    """Прибор возврата: суточный след в БД + метки `X-User-Id`/`X-Route` для nginx.

    Работает ПОСЛЕ ответа, чтобы не влиять на транзакцию запроса, и в
    threadpool'е, потому что запись синхронная, а мидлварь — корутина
    (иначе блокировали бы event loop). Ошибки инструментации глотаются внутри
    `record_request_activity` — прибор не имеет права ломать запрос.
    """
    response = await call_next(request)
    await run_in_threadpool(record_request_activity, request)
    set_activity_headers(request, response)
    return response


# Подключение роутеров
app.include_router(auth.router)
app.include_router(recommendations.router)
app.include_router(wishes.router)
app.include_router(users.router)
app.include_router(birthday_radar.router)
app.include_router(public.router)
app.include_router(og.router)
app.include_router(dev.router)

# Админка
setup_admin(app, engine)


# По умолчанию все GET-роуты должны поддерживать HEAD
def enable_head_for_get_routes(application: FastAPI) -> None:
    for route in application.routes:
        methods = getattr(route, 'methods', None)
        if methods and 'GET' in methods:
            route.methods = set(methods) | {'HEAD'}  # ty: ignore[unresolved-attribute]


class HolidayEvent(enum.Enum):
    NEW_YEAR = 'new_year'


PUSH_MESSAGES = {HolidayEvent.NEW_YEAR: ('🎄🎄🎄Скоро Новый год!🎄🎄🎄')}
BODY_MESSAGE = 'Заполните свой список желаний, чтобы друзья знали, что вам подарить'


@app.get('/debug-error')
async def trigger_error():
    # Намеренно кидаем ошибку для проверки доставки в трекер (Hawk).
    raise RuntimeError('Hawk debug error')


@app.get('/health')
async def health():
    # Liveness: процесс жив и отвечает. Дёргается docker-healthcheck'ом.
    # БД здесь намеренно не проверяем — её падение не должно перезапускать
    # здоровое приложение. Для проверки БД см. /health/ready.
    return {'status': 'ok'}


@app.get('/health/ready')
async def health_ready(db: Annotated[Session, Depends(get_db)]):
    # Readiness для внешнего мониторинга (uptime-бот): сервис реально способен
    # обслуживать запросы, т.е. БД доступна. Отдаёт 503, если нет.
    try:
        # SET LOCAL — таймаут в рамках текущей транзакции, чтобы при зависшей (не
        # мёртвой) БД эндпоинт быстро отдал 503, а не держал соединение бота.
        # get_db закрывает сессию с rollback, поэтому в пул значение не протекает.
        db.execute(text("SET LOCAL statement_timeout = '3s'"))
        db.execute(text('SELECT 1'))
    except Exception:
        logger.error('health/ready: БД недоступна')
        raise HTTPException(status_code=503, detail='db unavailable') from None
    return {'status': 'ok'}


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    # HEAD добавлен ко всем GET-роутам для рантайма (enable_head_for_get_routes),
    # но в OpenAPI он лишь дублирует GET (тот же operationId → "Duplicate Operation ID")
    # и засоряет контракт. Снимаем HEAD на время генерации схемы и возвращаем обратно.
    routes_with_head = [
        route
        for route in app.routes
        if (getattr(route, 'methods', None) or set()) >= {'GET', 'HEAD'}
    ]
    for route in routes_with_head:
        route.methods = set(route.methods) - {'HEAD'}  # ty: ignore[unresolved-attribute]
    try:
        openapi_schema = default_openapi()
    finally:
        for route in routes_with_head:
            route.methods = set(route.methods) | {'HEAD'}  # ty: ignore[unresolved-attribute]
    openapi_schema['components']['securitySchemes'] = {
        'ApiKey': {
            'type': 'apiKey',
            'name': 'Authorization',
            'in': 'header',
        }
    }
    openapi_schema['security'] = [
        {'ApiKey': []},
    ]
    app.openapi_schema = openapi_schema
    return app.openapi_schema


default_openapi = app.openapi
app.openapi = custom_openapi  # ty: ignore[invalid-assignment]

# Включаем HEAD для всех GET-роутов после регистрации всех эндпоинтов
enable_head_for_get_routes(app)
