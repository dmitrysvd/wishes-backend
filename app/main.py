import enum
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from hawk_python_sdk import Hawk

from app.admin.setup import setup_admin
from app.config import settings
from app.db import engine

# Реэкспорт для обратной совместимости
from app.dependencies import get_current_user, get_db
from app.helpers import get_user_deep_link
from app.logging import logger
from app.routers import auth, public, recommendations, users, wishes

__all__ = ['app', 'get_db', 'get_current_user', 'get_user_deep_link']

BASE_DIR = Path(__file__).parent.parent
APP_DIR = BASE_DIR / 'app'
TEMPLATES_DIR = APP_DIR / 'templates'

settings.LOGS_DIR.mkdir(exist_ok=True, parents=True)

# Hawk (hawk.so) — трекер ошибок. Без токена send() — безопасный no-op,
# поэтому объект создаём всегда. Их FastAPI-мидлварь не используем (она глотает
# исключение вместо ре-райза) — шлём ошибки вручную из internal_exception_handler.
# SDK допускает None в рантайме (no-op), но в их сигнатуре тип занижен.
hawk = Hawk(settings.HAWK_TOKEN)  # ty: ignore[invalid-argument-type]

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
            logger.exception('Exception')
            # Трейсбек берётся из sys.exc_info() — мы внутри except-блока.
            # Сбой трекера не должен ломать обработку запроса.
            try:
                hawk.send(exc)
            except Exception:
                logger.exception('Не удалось отправить ошибку в Hawk')
        raise exc
    return response


# Подключение роутеров
app.include_router(auth.router)
app.include_router(recommendations.router)
app.include_router(wishes.router)
app.include_router(users.router)
app.include_router(public.router)

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
    return {'status': 'ok'}


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = default_openapi()
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
