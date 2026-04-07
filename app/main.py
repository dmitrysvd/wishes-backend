import enum
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.admin.setup import setup_admin
from app.alerts import alert_exception
from app.config import settings
from app.db import engine

# Реэкспорт для обратной совместимости
from app.dependencies import get_current_user, get_db
from app.helpers import get_user_deep_link
from app.logging import logger
from app.routers import auth, users, wishes
from app.schemas import ChatMessageSchema

__all__ = ['app', 'get_db', 'get_current_user', 'get_user_deep_link']

BASE_DIR = Path(__file__).parent.parent
APP_DIR = BASE_DIR / 'app'
TEMPLATES_DIR = APP_DIR / 'templates'

settings.LOGS_DIR.mkdir(exist_ok=True, parents=True)

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
    )

app = FastAPI(
    title='Хотелки',
    root_path=settings.URL_ROOT_PATH,
)
templates = Jinja2Templates(directory=TEMPLATES_DIR)

if settings.IS_DEBUG:
    app.mount('/static', StaticFiles(directory=BASE_DIR / 'static'), name='static')
    app.mount('/media', StaticFiles(directory=settings.MEDIA_ROOT), name='media')

# TODO: ограничить CORS-origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
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
            alert_exception(request, exc)
        raise exc
    return response


# Подключение роутеров
app.include_router(auth.router)
app.include_router(wishes.router)
app.include_router(users.router)

# Админка
setup_admin(app, engine)


class HolidayEvent(enum.Enum):
    NEW_YEAR = 'new_year'


PUSH_MESSAGES = {HolidayEvent.NEW_YEAR: ('🎄🎄🎄Скоро Новый год!🎄🎄🎄')}
BODY_MESSAGE = 'Заполните свой список желаний, чтобы друзья знали, что вам подарить'


# WebSocket для чата
class WsConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str, exclude_self=True):
        for connection in self.active_connections:
            await connection.send_text(message)


ws_manager = WsConnectionManager()


@app.websocket('/chat')
async def ws_chat(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            message_raw = await websocket.receive_text()
            try:
                message = ChatMessageSchema.model_validate_json(message_raw)
            except ValidationError as ex:
                await websocket.send_json(
                    {'type': 'ERROR', 'code': 'INVALID_FORMAT', 'detail': ex.errors()}
                )
                continue
            await ws_manager.broadcast(message.model_dump_json())
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


@app.get('/sentry-debug')
async def trigger_error():
    pass


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
app.openapi = custom_openapi
