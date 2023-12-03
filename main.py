import json
from dataclasses import dataclass
from decimal import Decimal, getcontext
from pathlib import Path
from typing import Optional

import firebase_admin
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from firebase_admin import auth as firebase_auth
from firebase_admin.auth import ExpiredIdTokenError, verify_id_token
from firebase_admin.exceptions import FirebaseError
from sqladmin import Admin, ModelView
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from starlette.status import (
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)

from config import settings
from db import SessionLocal, User, Wish, engine
from firebase import (
    create_custom_firebase_token,
    get_firebase_app,
    get_firebase_user_data,
    get_or_create_firebase_user,
)
from schemas import (
    CurrentUserSchema,
    OtherUserSchema,
    RequestFirebaseAuthSchema,
    RequestVkAuthMobileSchema,
    ResponseVkAuthMobileSchema,
    ResponseVkAuthWebSchema,
    SavePushTokenSchema,
    WishReadSchema,
    WishWriteSchema,
)
from vk import (
    VkUserExtraData,
    exchange_tokens,
    get_vk_user_data_by_access_token,
    get_vk_user_friends,
)

app = FastAPI()
templates = Jinja2Templates(directory="templates")
BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=BASE_DIR / 'static'), name="static")

admin = Admin(app, engine)


class UserAdmin(ModelView, model=User):
    column_list = [User.id, User.display_name]


class WishAdmin(ModelView, model=Wish):
    column_list = [Wish.id, Wish.name]


if settings.IS_DEBUG:
    admin.add_view(UserAdmin)
    admin.add_view(WishAdmin)


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
        user_id = int(token.split(':')[-1])
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


def auth_vk(
    access_token: str, vk_extra_data: VkUserExtraData, db: Session
) -> tuple[str, str]:
    vk_basic_data = get_vk_user_data_by_access_token(access_token)

    firebase_uid = get_or_create_firebase_user(
        email=vk_extra_data.email,
        display_name=f'{vk_basic_data.first_name} {vk_basic_data.last_name}',
        photo_url=vk_basic_data.photo_url,
        phone=vk_extra_data.phone,
    )
    firebase_token = create_custom_firebase_token(firebase_uid)

    user = db.execute(
        select(User).where(User.vk_id == vk_basic_data.id)
    ).scalar_one_or_none()
    is_new_user = not bool(user)
    if is_new_user:
        friends_data = get_vk_user_friends(access_token)
        user = User(
            vk_id=vk_basic_data.id,
            vk_access_token=access_token,
            display_name=f'{vk_basic_data.first_name} {vk_basic_data.last_name}',
            photo_url=vk_basic_data.photo_url,
            phone=vk_extra_data.phone,
            email=vk_extra_data.email,
            firebase_uid=firebase_uid,
            gender=vk_basic_data.gender,
            vk_friends_data=friends_data,
        )
    else:
        user.vk_access_token = access_token
        user.firebase_uid = firebase_uid
    db.add(user)
    db.commit()

    return firebase_uid, firebase_token


@app.get('/auth/vk/web/')
def auth_vk_web(payload: str, db: Session = Depends(get_db)) -> ResponseVkAuthWebSchema:
    """
    Аутентификация через ВК в браузере.

    Открывается либо редиректом, либо при нажатии по кнопке скриптом js.
    payload передавается в том виде, в котором получен из Vk SDK.

    Возвращает данные для аутентификации в firebase.
    Создаст пользователя в firebase, если не существовал.
    """
    auth_payload = json.loads(payload)
    assert auth_payload['type'] == 'silent_token'
    silent_token = auth_payload['token']
    uuid = auth_payload['uuid']
    access_token, vk_extra_data = exchange_tokens(silent_token, uuid)
    firebase_uid, firebase_token = auth_vk(access_token, vk_extra_data, db)
    return ResponseVkAuthWebSchema(
        vk_access_token=access_token,
        firebase_uid=firebase_uid,
        firebase_token=firebase_token,
    )


@app.post('/auth/vk/mobile/')
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


# @app.post('/auth/firebase/', response_class=Response)
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
        decoded_token = verify_id_token(id_token, app=get_firebase_app())
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
        )
    else:
        user.firebase_uid = uid
    db.add(user)
    db.commit()


@app.post('/save_push_token/', response_class=Response)
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


@app.get('/auth/vk/index.html')
def auth_vk_page(request: Request):
    return templates.TemplateResponse(
        "registration.html",
        {
            "request": request,
            "vk_app_id": settings.VK_APP_ID,
            "auth_redirect_url": request.url_for('auth_vk_web'),
        },
    )


@app.get('/')
def main(request: Request, db: Session = Depends(get_db)):
    try:
        get_current_user(request)
    except HTTPException:
        return RedirectResponse(request.url_for('auth_vk_page'))
    return 'You are authenticated'


@app.get('/wishes', response_model=list[WishReadSchema])
def my_wishes(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.execute(select(Wish).where(Wish.user == user)).scalars()


@app.get('/wishes/{wish_id}', response_model=WishReadSchema)
def get_wish(wish_id: int, db: Session = Depends(get_db)):
    wish = db.scalars(select(Wish).where(Wish.id == wish_id)).one_or_none()
    if not wish:
        return HTTPException(HTTP_404_NOT_FOUND, 'Wish not found')
    return wish


@app.get('/wishes/user/{user_id}', response_model=list[WishReadSchema])
def user_wishes(user_id: int, db: Session = Depends(get_db)):
    user = db.execute(select(User).where(User.id == user_id)).scalar_one()
    return db.execute(select(Wish).where(Wish.user == user)).scalars()


@app.post('/wishes/user/{user_id}/reserve/{wish_id}', response_class=Response)
def reserve_wish(
    user_id: int,
    wish_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    other_user: Optional[User] = db.execute(
        select(User).where(User.id == user_id)
    ).scalar_one()
    if not other_user:
        raise HTTPException(HTTP_404_NOT_FOUND, 'User not found')
    wish = db.execute(
        select(Wish).where(Wish.user == other_user, Wish.id == wish_id)
    ).scalar_one_or_none()
    if not wish:
        raise HTTPException(HTTP_404_NOT_FOUND, 'Wish not found')
    if wish.reserved_by and wish.reserved_by != current_user:
        raise HTTPException(HTTP_403_FORBIDDEN, 'Reserved by someone else')
    wish.reserved_by = current_user
    db.add(wish)
    db.commit()


@app.post(
    '/wishes/user/{user_id}/cancel_reservation/{wish_id}', response_class=Response
)
def cancel_wish_reservation(
    user_id: int,
    wish_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    other_user: Optional[User] = db.execute(
        select(User).where(User.id == user_id)
    ).scalar_one_or_none()
    if not other_user:
        raise HTTPException(404, 'User not found')
    wish = db.execute(
        select(Wish).where(Wish.user == other_user, Wish.id == wish_id)
    ).scalar_one_or_none()
    if not wish:
        raise HTTPException(404, 'Wish not found')
    if wish.reserved_by and wish.reserved_by != current_user:
        raise HTTPException(HTTP_403_FORBIDDEN, 'Reserved by someone else')
    wish.reserved_by = None
    db.add(wish)
    db.commit()


@app.post('/wishes')
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
    )
    db.add(wish)
    db.commit()


@app.put('/wishes/{wish_id}')
def update_wish(
    wish_id: int,
    wish_data: WishWriteSchema,
    db: Session = Depends(get_db),
):
    wish = db.execute(select(Wish).where(Wish.id == wish_id)).scalar_one_or_none()
    if not wish:
        raise HTTPException(HTTP_404_NOT_FOUND, 'Wish not found')
    wish.name = wish_data.name
    wish.description = wish_data.description
    wish.price = Decimal(wish_data.price) if wish_data.price else None
    db.add(wish)
    db.commit()


@app.delete('/wishes/{wish_id}')
def delete_wish(wish_id: int, db: Session = Depends(get_db)):
    db.execute(delete(Wish).where(Wish.id == wish_id))
    db.commit()


@app.get('/users/', response_model=list[OtherUserSchema])
def users(db: Session = Depends(get_db)):
    return db.execute(select(User)).scalars()


@app.get('/users/{user_id}', response_model=OtherUserSchema)
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = db.scalars(select(User).where(User.id == user_id)).one_or_none()
    if not user:
        return HTTPException(HTTP_404_NOT_FOUND, 'User not found')
    return user


@app.get('/users/search/', response_model=list[OtherUserSchema])
def search_users(
    q: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Поиск пользователей по имени, email и номеру. Возвращает первые 20 результатов."""
    return db.execute(
        select(User)
        .where(
            User.id != user.id,
        )
        .where(
            User.display_name.icontains(q)
            | User.email.icontains(q)
            | User.phone.icontains(q)
        )
        .limit(20)
    ).scalars()


@app.get('/users/me/', response_model=CurrentUserSchema)
def users_me(user: User = Depends(get_current_user)):
    return user


@app.post('/delete_own_account/')
def delete_own_account(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    # TODO: удалять пользователя firebase и сбрасывать access_token.
    db.execute(delete(User).where(User.id == user.id))
    db.commit()


@app.post('/follow/{follow_user_id}')
def follow_user(
    follow_user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    follow_user = db.execute(select(User).where(User.id == follow_user_id)).scalar_one()
    user.follows.append(follow_user)
    db.commit()


@app.post('/unfollow/{unfollow_user_id}')
def unfollow_user(
    unfollow_user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    unfollow_user = db.execute(
        select(User).where(User.id == unfollow_user_id)
    ).scalar_one()
    user.follows.remove(unfollow_user)


@app.post('/possible_friends/', response_model=list[OtherUserSchema])
def possible_friends(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.vk_friends_data:
        return []
    vk_friend_ids = [vk_friend_data['id'] for vk_friend_data in user.vk_friends_data]
    return db.execute(select(User).where(User.vk_id.in_(vk_friend_ids))).scalars()


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title='Хотелки',
        version='0.0.1',
        routes=app.routes,
    )
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


app.openapi = custom_openapi
