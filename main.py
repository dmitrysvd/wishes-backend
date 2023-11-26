import json
from dataclasses import dataclass
from pathlib import Path

import firebase_admin
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from firebase_admin import auth as firebase_auth
from firebase_admin.auth import verify_id_token
from firebase_admin.exceptions import FirebaseError
from sqlalchemy.orm import Session
from starlette.status import HTTP_401_UNAUTHORIZED

from config import settings
from db import SessionLocal, User, Wish
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
    ResponseAuthSchema,
    SavePushTokenSchema,
    VkAuthViaSilentTokenSchema,
    WishReadSchema,
    WishWriteSchema,
)
from vk import auth_vk_user_by_silent_token

app = FastAPI()
templates = Jinja2Templates(directory="templates")
BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=BASE_DIR / 'static'), name="static")


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

    if settings.IS_DEBUG and token == settings.TEST_TOKEN:
        user = db.query(User).first()
        if not user:
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED, detail='Not authenticated'
            )
        return user

    decoded_token = verify_id_token(token, app=get_firebase_app())
    uid = decoded_token['uid']
    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED, detail='Not authenticated'
        )
    return user


def auth_vk_via_silent_token(silent_token: str, uuid: str) -> ResponseAuthSchema:
    vk_user = auth_vk_user_by_silent_token(silent_token, uuid)
    firebase_uid = get_or_create_firebase_user(
        email=vk_user.email,
        display_name=f'{vk_user.first_name} {vk_user.last_name}',
        photo_url=vk_user.photo_url,
        phone=vk_user.phone,
    )
    firebase_token = create_custom_firebase_token(firebase_uid)

    with SessionLocal() as db:
        user = db.query(User).filter(User.vk_id == vk_user.id).first()
        is_new_user = not bool(user)
        if is_new_user:
            user = User(
                vk_id=vk_user.id,
                vk_access_token=vk_user.access_token,
                first_name=vk_user.first_name,
                last_name=vk_user.last_name,
                display_name=f'{vk_user.first_name} {vk_user.last_name}',
                photo_url=vk_user.photo_url,
                phone=vk_user.phone,
                email=vk_user.email,
                firebase_uid=firebase_uid,
            )
        else:
            user.vk_access_token = vk_user.access_token
        db.add(user)
        db.commit()

    return ResponseAuthSchema(
        vk_access_token=vk_user.access_token,
        firebase_uid=firebase_uid,
        firebase_token=firebase_token,
    )


@app.get('/auth/vk/web/', response_model=ResponseAuthSchema)
def complete_auth_vk_web(payload: str):
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
    return auth_vk_via_silent_token(silent_token, uuid)


@app.post('/auth/vk/mobile/', response_model=ResponseAuthSchema)
def auth_vk_mobile(schema: VkAuthViaSilentTokenSchema):
    """
    Аутентификация через ВК на мобильных устройствах.

    Нужно передать silent_token, чтобы бэк смог получить
    данные пользователя при обмене silent_token на access_token.

    Возвращает данные для аутентификации в firebase.
    Создаст пользователя в firebase, если не существовал.
    """
    return auth_vk_via_silent_token(
        silent_token=schema.silent_token,
        uuid=schema.uuid,
    )


@app.post('/auth/firebase/', response_class=Response)
def auth_firebase(
    firebase_auth_schema: RequestFirebaseAuthSchema, db: Session = Depends(get_db)
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

    user = db.query(User).filter(User.firebase_uid == uid).first()
    if not user and firebase_user.email_verified:
        user = db.query(User).filter(User.email == firebase_user.email).first()

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
            "auth_redirect_url": request.url_for('complete_auth_vk_web'),
        },
    )


@app.get('/')
def main(request: Request, db: Session = Depends(get_db)):
    try:
        get_current_user(request)
    except HTTPException:
        return RedirectResponse(request.url_for('auth_vk_page'))
    return 'You are authenticated'


@app.get('/wishes')
def my_wishes(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[WishReadSchema]:
    wishes = db.query(Wish).filter(Wish.user == user)
    return [
        WishReadSchema.model_validate(wish, from_attributes=True) for wish in wishes
    ]


@app.get('/wishes/user/{user_id}')
def user_wishes(user_id: int, db: Session = Depends(get_db)) -> list[WishReadSchema]:
    user = db.query(User).get(user_id)
    wishes = db.query(Wish).filter(Wish.user == user)
    return [
        WishReadSchema.model_validate(wish, from_attributes=True) for wish in wishes
    ]


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
):
    with SessionLocal() as session:
        wish = session.query(Wish).get(wish_id)
        if not wish:
            raise Exception
        wish.name = wish_data.name
        wish.description = wish_data.description
        wish.price = wish_data.price
        session.add(wish)
        session.commit()


@app.delete('/wishes/{wish_id}')
def delete_wish(wish_id: int):
    with SessionLocal() as session:
        session.query(Wish).filter(Wish.id == wish_id).delete()


@app.get('/users/')
def users(db: Session = Depends(get_db)) -> list[OtherUserSchema]:
    users = db.query(User).all()
    return [
        OtherUserSchema.model_validate(user, from_attributes=True) for user in users
    ]


@app.get('/users/me/')
def users_me(user: User = Depends(get_current_user)) -> CurrentUserSchema:
    return CurrentUserSchema.model_validate(user, from_attributes=True)


@app.post('/delete_own_account/')
def delete_own_account(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    db.query(User).filter(User.id == user.id).delete()
    db.commit()


@app.post('/follow/{follow_user_id}')
def follow_user(
    follow_user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    follow_user = db.query(User).filter(User.id == follow_user_id).first()
    if not follow_user:
        raise Exception('Не найден пользователь для добавления в follows')
    user.follows.append(follow_user)
    print()
    db.commit()


@app.post('/unfollow/{unfollow_user_id}')
def unfollow_user(
    unfollow_user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    unfollow_user = db.query(User).filter(User.id == unfollow_user_id).first()
    if not unfollow_user:
        raise Exception('Не найден пользователь для удаления из follows')
    user.follows.remove(unfollow_user)


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
