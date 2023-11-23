import json
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.status import HTTP_401_UNAUTHORIZED

from config import settings
from db import SessionLocal, User, Wish
from schemas import (
    RequestFirebaseAuthSchema,
    ResponseAuthSchema,
    UserSchema,
    WishReadSchema,
    WishWriteSchema,
)


@dataclass(frozen=True)
class _ExtraUserData:
    first_name: str
    last_name: str
    photo_url: str


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

    user = db.query(User).filter(User.vk_access_token == token).first()
    if not user:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED, detail='Not authenticated'
        )
    return user


def get_user_data_by_silent_token(silent_token: str, uuid: str) -> _ExtraUserData:
    response = httpx.post(
        'https://api.vk.com/method/auth.getProfileInfoBySilentToken',
        params={
            "v": "5.108",
            "access_token": settings.VK_SERVICE_KEY,
            "token": [silent_token],
            "uuid": [uuid],
            "event": [""],
        },
    )
    response.raise_for_status()
    response_json = response.json()
    if response_json['response'].get('errors', []):
        raise HTTPException(status_code=401, detail="Not authenticated")
    photo_url = response_json['response']['success'][0]['photo_200']
    first_name = response_json['response']['success'][0]['first_name']
    last_name = response_json['response']['success'][0]['last_name']
    return _ExtraUserData(
        photo_url=photo_url,
        first_name=first_name,
        last_name=last_name,
    )


def get_user_data(vk_id: str, access_token: str):
    response = httpx.get(
        'https://api.vk.com/method/users.get',
        params={
            'v': '5.131',
            'access_token': settings.VK_SERVICE_KEY,
            'user_ids': [vk_id],
        },
    )
    data = response.json()
    print()
    return data


def exchange_tokens(silent_token: str, uuid: str) -> tuple[str, str]:
    response = httpx.post(
        'https://api.vk.com/method/auth.exchangeSilentAuthToken',
        data={
            'v': '5.131',
            'token': silent_token,
            'access_token': settings.VK_SERVICE_KEY,
            'uuid': uuid,
        },
    )
    response.raise_for_status()
    response_json = response.json()
    if 'error' in response_json:
        raise HTTPException(status_code=401, detail="Not authenticated")
    response.raise_for_status()
    response_json = response.json()
    if 'error' in response_json:
        raise HTTPException(status_code=401, detail="Not authenticated")
    access_token = response_json['response']['access_token']
    vk_user_id = response_json['response']['user_id']
    return access_token, str(vk_user_id)


@app.get('/auth/vk/web/', response_model=ResponseAuthSchema)
def complete_auth_vk_web(request: Request, payload: str):
    auth_payload = json.loads(payload)
    assert auth_payload['type'] == 'silent_token'
    silent_token = auth_payload['token']
    uuid = auth_payload['uuid']
    with SessionLocal() as db:
        extra_user_data = get_user_data_by_silent_token(
            silent_token=silent_token, uuid=uuid
        )
        access_token, vk_user_id = exchange_tokens(silent_token, uuid)
        user = db.query(User).filter(User.vk_id == vk_user_id).first()
        if user:
            user.vk_access_token = access_token
            db.add(user)
        else:
            user = User(
                vk_id=vk_user_id,
                access_token=access_token,
                first_name=extra_user_data.first_name,
                last_name=extra_user_data.last_name,
                photo_url=extra_user_data.photo_url,
            )
            db.add(user)
        db.commit()
    return {'access_token': access_token}


@app.post('/auth/firebase/', response_model=ResponseAuthSchema)
def auth_firebase(firebase_auth: RequestFirebaseAuthSchema):
    id_token = firebase_auth.id_token
    return {'access_token': 'very_secret_token'}


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
def main(request: Request):
    try:
        get_current_user(request, db=SessionLocal())
    except HTTPException:
        return RedirectResponse(request.url_for('auth_vk_page'))
    return ['You are authenticated']


@app.get('/wishes')
def my_wishes(user: User = Depends(get_current_user)) -> list[WishReadSchema]:
    with SessionLocal() as session:
        wishes = session.query(Wish).filter(Wish.user == user)
    return [
        WishReadSchema.model_validate(wish, from_attributes=True) for wish in wishes
    ]


@app.get('/wishes/user/{user_id}')
def user_wishes(user_id: int) -> list[WishReadSchema]:
    with SessionLocal() as session:
        user = session.query(User).get(user_id)
        wishes = session.query(Wish).filter(Wish.user == user)
    return [
        WishReadSchema.model_validate(wish, from_attributes=True) for wish in wishes
    ]


@app.post('/wishes')
def add_wish(wish_data: WishWriteSchema, user: User = Depends(get_current_user)):
    with SessionLocal() as session:
        wish = Wish(
            user_id=user.id,
            name=wish_data.name,
            description=wish_data.description,
            price=wish_data.price,
        )
        session.add(wish)
        session.commit()


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


@app.get('/users/me/')
def users_me(user: User = Depends(get_current_user)) -> UserSchema:
    return UserSchema.model_validate(user, from_attributes=True)
