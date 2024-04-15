import enum
import json
import os
import re
import sys
from base64 import b64encode
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, getcontext
from hashlib import md5
from pathlib import Path
from typing import Annotated, Optional, Union
from uuid import UUID

import firebase_admin
import httpx
from bs4 import BeautifulSoup
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from firebase_admin import auth as firebase_auth
from firebase_admin.auth import ExpiredIdTokenError, verify_id_token
from firebase_admin.exceptions import FirebaseError
from loguru import logger
from pydantic import HttpUrl
from sqladmin import Admin, ModelView
from sqlalchemy import Select, delete, select, update
from sqlalchemy.orm import Query, Session
from starlette.status import (
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)

from app.alerts import alert_exception
from app.config import settings
from app.db import SessionLocal, User, Wish, engine
from app.firebase import (
    create_custom_firebase_token,
    create_firebase_user,
    delete_firebase_user,
    get_firebase_app,
    get_firebase_user_data,
    send_push,
)
from app.parsers import ItemInfoParseError, try_parse_item_by_link
from app.schemas import (
    AnnotatedOtherUserSchema,
    CurrentUserReadSchema,
    CurrentUserUpdateSchema,
    ItemInfoRequestSchema,
    ItemInfoResponseSchema,
    OtherUserSchema,
    RequestFirebaseAuthSchema,
    RequestVkAuthMobileSchema,
    RequestVkAuthWebSchema,
    ResponseVkAuthMobileSchema,
    ResponseVkAuthWebSchema,
    SavePushTokenSchema,
    WishReadSchema,
    WishWriteSchema,
)
from app.utils import utc_now
from app.vk import (
    VkUserExtraData,
    exchange_tokens,
    get_vk_user_data_by_access_token,
    get_vk_user_friends,
)

BASE_DIR = Path(__file__).parent.parent
APP_DIR = BASE_DIR / 'app'
TEMPLATES_DIR = APP_DIR / 'templates'
WISH_IMAGES_DIR = settings.MEDIA_ROOT / 'wish_images'
PROFILE_IMAGES_DIR = settings.MEDIA_ROOT / 'profile_images'

settings.LOGS_DIR.mkdir(exist_ok=True, parents=True)

app = FastAPI(
    title='Хотелки',
    root_path=settings.URL_ROOT_PATH,
)
templates = Jinja2Templates(directory=TEMPLATES_DIR)

if settings.IS_DEBUG:
    app.mount('/static', StaticFiles(directory=settings.STATIC_ROOT), name='static')
    app.mount('/media', StaticFiles(directory=settings.MEDIA_ROOT), name='media')

# TODO: ограничить CORS-origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

admin = Admin(app, engine)

logger.add(settings.LOGS_DIR / 'log.log')

AUTH_TAG = 'auth'
WISHES_TAG = 'wishes'
USERS_TAG = 'users'


class UserAdmin(ModelView, model=User):
    column_list = [User.id, User.display_name]


class WishAdmin(ModelView, model=Wish):
    column_list = [Wish.id, Wish.name]


if settings.IS_DEBUG:
    admin.add_view(UserAdmin)
    admin.add_view(WishAdmin)


class HolidayEvent(enum.Enum):
    NEW_YEAR = 'new_year'


PUSH_MESSAGES = {HolidayEvent.NEW_YEAR: ('🎄🎄🎄Скоро Новый год!🎄🎄🎄')}

BODY_MESSAGE = 'Заполните свой список желаний, чтобы друзья знали, что вам подарить'


@app.middleware('http')
async def internal_exception_handler(request: Request, call_next):
    try:
        response = await call_next(request)
    except Exception as exc:
        if not settings.IS_DEBUG:
            await alert_exception(request, exc)
        raise exc
    return response


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
        decoded_token = verify_id_token(token, app=get_firebase_app())
    except ExpiredIdTokenError:
        raise HTTPException(HTTP_401_UNAUTHORIZED, 'Token expired')
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


def auth_vk(
    access_token: str,
    vk_extra_data: VkUserExtraData,
    db: Session,
) -> tuple[str, str]:
    vk_basic_data = get_vk_user_data_by_access_token(access_token)

    user = db.scalars(select(User).where(User.vk_id == vk_basic_data.id)).one_or_none()
    if not user and vk_extra_data.email:
        user = db.scalars(
            select(User).where(User.email == vk_extra_data.email)
        ).one_or_none()

    is_new_user = not bool(user)
    if is_new_user:
        firebase_uid = create_firebase_user(
            email=vk_extra_data.email,
            display_name=f'{vk_basic_data.first_name} {vk_basic_data.last_name}',
            photo_url=vk_basic_data.photo_url,
            phone=vk_extra_data.phone,
        )
        user = User(
            vk_id=vk_basic_data.id,
            vk_access_token=access_token,
            display_name=f'{vk_basic_data.first_name} {vk_basic_data.last_name}',
            photo_url=vk_basic_data.photo_url,
            phone=vk_extra_data.phone,
            email=vk_extra_data.email,
            firebase_uid=firebase_uid,
            birth_date=vk_basic_data.birthdate,
            gender=vk_basic_data.gender,
            registered_at=utc_now(),
        )
    else:
        firebase_uid = user.firebase_uid

    user.vk_access_token = access_token
    if not user.vk_id:
        # Если пользователь раньше был зареган через google.
        user.vk_id = str(vk_basic_data.id)
        user.vk_friends_data = get_vk_user_friends(access_token)

    db.add(user)
    db.commit()

    if is_new_user:
        logger.info(
            'Зарегистрирован новый пользователь: firebase_uid={firebase_uid}',
            firebase_uid=firebase_uid,
        )

    firebase_token = create_custom_firebase_token(firebase_uid)
    return firebase_uid, firebase_token


@app.post('/auth/vk/web', tags=[AUTH_TAG])
def auth_vk_web(
    request_data: RequestVkAuthWebSchema,
    db: Session = Depends(get_db),
) -> ResponseVkAuthWebSchema:
    """
    Аутентификация через ВК в вебе.

    Возвращает данные для аутентификации в firebase.
    Создаст пользователя в firebase, если не существовал.
    """
    silent_token = request_data.silent_token
    uuid = request_data.uuid
    access_token, vk_extra_data = exchange_tokens(silent_token, uuid)
    firebase_uid, firebase_token = auth_vk(access_token, vk_extra_data, db)
    return ResponseVkAuthWebSchema(
        vk_access_token=access_token,
        firebase_uid=firebase_uid,
        firebase_token=firebase_token,
    )


@app.post('/auth/vk/mobile', tags=[AUTH_TAG])
def auth_vk_mobile(
    auth_data: RequestVkAuthMobileSchema, db: Session = Depends(get_db)
) -> ResponseVkAuthMobileSchema:
    """
    Аутентификация через ВК на мобильных устройствах.

    Создаст пользователя в firebase и на сервере, если не существовал.
    Возвращает данные для аутентификации в firebase.
    """
    access_token = auth_data.access_token
    vk_extra_data = VkUserExtraData(email=auth_data.email, phone=auth_data.phone)
    firebase_uid, firebase_token = auth_vk(access_token, vk_extra_data, db)
    return ResponseVkAuthMobileSchema(
        firebase_uid=firebase_uid,
        firebase_token=firebase_token,
    )


@app.post('/auth/firebase', response_class=Response, tags=[AUTH_TAG])
def auth_firebase(
    firebase_auth_schema: RequestFirebaseAuthSchema,
    db: Session = Depends(get_db),
):
    """
    Аутентификация через firebase Google.

    Клиент уже должен быть залогинен в firebase.
    Если пользователя с email из firebase нет в БД, создаст его.
    Если пользователь уже есть, ничего не делает.
    """
    id_token = firebase_auth_schema.id_token
    try:
        decoded_token = verify_id_token(id_token)
    except FirebaseError as ex:
        raise HTTPException(status_code=403, detail="Not authenticated")
    uid = decoded_token['uid']
    firebase_user = get_firebase_user_data(uid)

    user = db.execute(select(User).where(User.firebase_uid == uid)).scalar_one_or_none()
    if not user and firebase_user.email_verified:
        user = db.execute(
            select(User).where(User.email == firebase_user.email)
        ).scalar_one_or_none()

    is_new_user = not bool(user)
    if is_new_user:
        user = User(
            display_name=firebase_user.display_name,
            photo_url=firebase_user.photo_url,
            phone=firebase_user.phone_number,
            email=firebase_user.email,
            firebase_uid=uid,
            registered_at=utc_now(),
        )
    else:
        user.firebase_uid = uid

    db.add(user)
    db.commit()

    if is_new_user:
        logger.info(
            'Зарегистрирован новый пользователь: firebase_uid={firebase_uid}',
            firebase_uid=uid,
        )


@app.post('/save_push_token', response_class=Response, tags=[AUTH_TAG])
def save_push_token(
    schema: SavePushTokenSchema,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Сохранить токен для отправки пушей на мобилки.

    Вызывается после аутентификации через vk или firebase.
    """
    user.firebase_push_token = schema.push_token
    db.add(user)
    db.commit()


@app.post('/wishes', tags=[WISHES_TAG], response_model=WishReadSchema)
def add_wish(
    wish_data: WishWriteSchema,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    wish = Wish(
        user_id=user.id,
        name=wish_data.name,
        description=wish_data.description,
        price=wish_data.price,
        link=str(wish_data.link) if wish_data.link else None,
    )
    db.add(wish)
    db.commit()
    return wish


@app.get('/wishes', response_model=list[WishReadSchema], tags=[WISHES_TAG])
def my_wishes(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = Wish.get_active_wish_query().where(Wish.user == user)
    return db.scalars(query)


@app.get('/reserved_wishes', response_model=list[WishReadSchema], tags=[WISHES_TAG])
def my_reserved_wishes(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    query = Wish.get_active_wish_query().where(Wish.reserved_by == user)
    return db.scalars(query)


@app.get('/wishes/{wish_id}', response_model=WishReadSchema, tags=[WISHES_TAG])
def get_wish(
    wish_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    wish = db.scalars(select(Wish).where(Wish.id == wish_id)).one_or_none()
    if not wish or (wish.is_archived and wish.user != user):
        raise HTTPException(HTTP_404_NOT_FOUND, 'Wish not found')
    return wish


@app.put('/wishes/{wish_id}', tags=[WISHES_TAG])
def update_wish(
    wish_data: WishWriteSchema,
    db: Session = Depends(get_db),
    wish: Wish = Depends(get_current_user_wish),
):
    wish.name = wish_data.name
    wish.description = wish_data.description
    wish.price = Decimal(wish_data.price) if wish_data.price else None
    wish.link = str(wish_data.link) if wish_data.link else None
    db.add(wish)
    db.commit()


@app.delete('/wishes/{wish_id}', tags=[WISHES_TAG])
def delete_wish(
    db: Session = Depends(get_db),
    wish: Wish = Depends(get_current_user_wish),
):
    db.delete(wish)
    db.commit()


@app.post('/wishes/{wish_id}/image', tags=[WISHES_TAG])
def upload_wish_image(
    file: UploadFile,
    wish: Wish = Depends(get_current_user_wish),
    db: Session = Depends(get_db),
):
    WISH_IMAGES_DIR.mkdir(exist_ok=True, parents=True)
    content = file.file.read()
    content_hash = md5(content).hexdigest()
    file_name = f'{content_hash}'
    file_path = WISH_IMAGES_DIR / file_name
    file_path.write_bytes(content)
    wish.image = file_name
    db.add(wish)
    db.commit()


@app.delete('/wishes/{wish_id}/image', tags=[WISHES_TAG])
def delete_wish_image(
    wish: Wish = Depends(get_current_user_wish),
    db: Session = Depends(get_db),
):
    wish.image = None
    db.add(wish)
    db.commit()


@app.get(
    '/users/{user_id}/wishes', response_model=list[WishReadSchema], tags=[WISHES_TAG]
)
def user_wishes(user_id: UUID, db: Session = Depends(get_db)):
    user = db.scalars(select(User).where(User.id == user_id)).one()
    query = Wish.get_active_wish_query().where(Wish.user == user)
    return db.scalars(query)


@app.get('/reserved_wishes', response_model=list[WishReadSchema], tags=[WISHES_TAG])
def reserved_wishes(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    query = Wish.get_active_wish_query().where(Wish.reserved_by == user)
    return db.scalars(query)


@app.post('/wishes/{wish_id}/reserve', response_class=Response, tags=[WISHES_TAG])
def reserve_wish(
    wish_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = Wish.get_active_wish_query().where(Wish.id == wish_id)
    wish = db.scalars(query).one_or_none()
    if not wish:
        raise HTTPException(HTTP_404_NOT_FOUND, 'Wish not found')
    if wish.reserved_by and wish.reserved_by != current_user:
        raise HTTPException(HTTP_403_FORBIDDEN, 'Reserved by someone else')
    wish.reserved_by = current_user
    db.add(wish)
    db.commit()


@app.post(
    '/wishes/{wish_id}/cancel_reservation', response_class=Response, tags=[WISHES_TAG]
)
def cancel_wish_reservation(
    wish_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    wish = db.execute(select(Wish).where(Wish.id == wish_id)).scalar_one_or_none()
    if not wish:
        raise HTTPException(404, 'Wish not found')
    if wish.reserved_by and wish.reserved_by != current_user:
        raise HTTPException(HTTP_403_FORBIDDEN, 'Reserved by someone else')
    wish.reserved_by = None
    db.add(wish)
    db.commit()


@app.post('/wishes/{wish_id}/archive', response_class=Response, tags=[WISHES_TAG])
def archive_wish(
    db: Session = Depends(get_db), wish: Wish = Depends(get_current_user_wish)
):
    wish.is_archived = True
    db.add(wish)
    db.commit()


@app.post('/wishes/{wish_id}/unarchive', response_class=Response, tags=[WISHES_TAG])
def unarchive_wish(
    db: Session = Depends(get_db), wish: Wish = Depends(get_current_user_wish)
):
    wish.is_archived = False
    db.add(wish)
    db.commit()


@app.get('/archived_wishes', response_model=list[WishReadSchema], tags=[WISHES_TAG])
def archived_wishes(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return db.scalars(select(Wish).where(Wish.user == user, Wish.is_archived == True))


@app.get('/users/search', response_model=list[OtherUserSchema], tags=[USERS_TAG])
def search_users(
    q: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Поиск пользователей по имени, email и номеру. Возвращает первые 20 результатов."""
    query = (
        select(User)
        .where(
            (User.id != user.id)
            & (
                User.display_name.icontains(q.capitalize())
                | User.display_name.icontains(q.lower())
                | User.email.icontains(q)
                | User.phone.icontains(q)
            )
        )
        .limit(20)
    )
    return db.execute(query).scalars()


def get_annotated_users(
    db: Session,
    current_user: User,
    outer_users: Union[Select[tuple[User]], list[User], None] = None,
) -> list[AnnotatedOtherUserSchema]:
    query = select(
        User,
        User.followed_by.any(User.id == current_user.id).label('followed_by_me'),
        User.follows.any(User.id == current_user.id).label('follows_me'),
    )
    if isinstance(outer_users, Select):
        user_ids = [user.id for user in db.execute(outer_users).scalars()]
        query = query.where(User.id.in_(user_ids))
    elif isinstance(outer_users, list):
        user_ids = [user.id for user in outer_users]
        query = query.where(User.id.in_(user_ids))
    values = db.execute(query).all()
    for user, followed_by_me, follows_me in values:
        user.followed_by_me = followed_by_me  # type: ignore
        user.follows_me = follows_me  # type: ignore
    return [AnnotatedOtherUserSchema.model_validate(val[0]) for val in values]


def get_user_deep_link(user: User) -> str:
    return f'https://hotelki.pro/user?userId={user.id}#'


@app.get('/users/', response_model=list[AnnotatedOtherUserSchema], tags=[USERS_TAG])
def users(db: Session = Depends(get_db)):
    user = db.execute(select(User).limit(1)).scalar_one()
    return get_annotated_users(db, user)


@app.get('/users/me', response_model=CurrentUserReadSchema, tags=[USERS_TAG])
def users_me(user: User = Depends(get_current_user)):
    return user


@app.put('/users/me', tags=[USERS_TAG])
def update_profile(
    update_data: CurrentUserUpdateSchema,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user.birth_date = update_data.birth_date
    user.display_name = update_data.display_name
    user.gender = update_data.gender
    db.add(user)
    db.commit()


def delete_user_image(user: User, db: Session):
    user.photo_path = None
    user.photo_url = None
    if user.photo_path:
        os.remove(user.photo_path)
    db.add(user)
    db.commit()


@app.post('/set_profile_image', tags=[USERS_TAG])
def set_profile_image(
    request: Request,
    image: UploadFile,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    PROFILE_IMAGES_DIR.mkdir(exist_ok=True, parents=True)
    content = image.file.read()
    file_name = f'profile_image_user_{user.id}_{datetime.now().isoformat()}'
    file_path = PROFILE_IMAGES_DIR / file_name
    file_path.write_bytes(content)
    related_media_path = file_path.relative_to(settings.MEDIA_ROOT)
    user.photo_url = (
        f"{request.url.scheme}://{request.headers['host']}/media/{related_media_path}"
    )
    user.photo_path = str(file_path)
    db.add(user)
    db.commit()


@app.post('/delete_profile_image', tags=[USERS_TAG])
def delete_profile_image(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.photo_path:
        delete_user_image(user, db)


@app.get('/users/{user_id}', response_model=AnnotatedOtherUserSchema, tags=[USERS_TAG])
def get_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user = db.scalars(select(User).where(User.id == user_id)).one_or_none()
    if not user:
        raise HTTPException(HTTP_404_NOT_FOUND, 'User not found')
    return get_annotated_users(db, current_user, [user])[0]


@app.post('/delete_own_account', tags=[USERS_TAG])
def delete_own_account(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    delete_firebase_user(user.firebase_uid)
    db.delete(user)
    db.commit()


@app.get(
    '/users/{user_id}/followers',
    tags=[USERS_TAG],
    response_model=list[AnnotatedOtherUserSchema],
)
def user_followers(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user = db.scalars(select(User).where(User.id == user_id)).one()
    return get_annotated_users(db, current_user, user.followed_by)


@app.get(
    '/users/{user_id}/follows',
    tags=[USERS_TAG],
    response_model=list[AnnotatedOtherUserSchema],
)
def users_followed_by_this_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user = db.scalars(select(User).where(User.id == user_id)).one()
    return get_annotated_users(db, current_user, user.follows)


def send_push_about_new_follower(target: User, follower: User):
    if not target.firebase_push_token:
        return
    send_push(
        push_tokens=[target.firebase_push_token],
        title='У вас новый подписчик',
        body=f'На вас подписался {follower.display_name}',
        link=get_user_deep_link(follower),
    )
    logger.info(f'Отправлен пуш при подписании {follower.id} на {target.id}')


@app.post('/follow/{follow_user_id}', tags=[USERS_TAG])
def follow_user(
    follow_user_id: UUID,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    follow_user = db.execute(select(User).where(User.id == follow_user_id)).scalar_one()
    if follow_user in user.follows:
        return
    user.follows.append(follow_user)
    db.commit()
    background_tasks.add_task(
        send_push_about_new_follower,
        target=follow_user,
        follower=user,
    )


@app.post('/unfollow/{unfollow_user_id}', tags=[USERS_TAG])
def unfollow_user(
    unfollow_user_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    unfollow_user = db.execute(
        select(User).where(User.id == unfollow_user_id)
    ).scalar_one()
    if unfollow_user not in user.follows:
        return
    user.follows.remove(unfollow_user)
    db.commit()


@app.get('/possible_friends', tags=[USERS_TAG])
def possible_friends(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AnnotatedOtherUserSchema]:
    if not user.vk_friends_data:
        return []
    vk_friend_ids = [vk_friend_data['id'] for vk_friend_data in user.vk_friends_data]
    query = (
        select(User)
        .where(User.vk_id.in_(vk_friend_ids))
        .where(~User.followed_by.any(User.id == user.id))
    )
    return get_annotated_users(db, user, query)


@app.post('/item_info_from_page')
async def get_item_info_from_page(
    request_data: ItemInfoRequestSchema,
    user: User = Depends(get_current_user),
) -> ItemInfoResponseSchema:
    try:
        result = await try_parse_item_by_link(str(request_data.link), request_data.html)
        logger.debug('result return value {result}', result=result)
    except ItemInfoParseError as ex:
        logger.warning(str(ex))
        result = None
        if request_data.html:
            logger.info(f'Перезапрос html от сервера для превью: {request_data.link}')
            try:
                result = await try_parse_item_by_link(str(request_data.link))
            except ItemInfoParseError:
                logger.warning(str(ex))
    if result is None:
        raise HTTPException(detail='Ошибка получения данных', status_code=400)
    return result


@app.get('/invite_link/')
def get_invite_link(user: User = Depends(get_current_user)):
    return 'https://hotelki.pro'


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
