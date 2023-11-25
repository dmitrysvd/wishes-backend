import json
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from firebase_admin.auth import verify_id_token
from firebase_admin.exceptions import FirebaseError
from sqlalchemy.orm import Session
from starlette.status import HTTP_401_UNAUTHORIZED

from config import settings
from db import SessionLocal, User, Wish
from firebase import get_firebase_app
from schemas import (
    PrivateUserSchema,
    RequestFirebaseAuthSchema,
    ResponseAuthSchema,
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

    user = db.query(User).filter(User.vk_access_token == token).first()
    if not user:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED, detail='Not authenticated'
        )
    return user


@app.get('/auth/vk/web/', response_model=ResponseAuthSchema)
def complete_auth_vk_web(request: Request, payload: str):
    auth_payload = json.loads(payload)
    assert auth_payload['type'] == 'silent_token'
    silent_token = auth_payload['token']
    uuid = auth_payload['uuid']
    vk_user = auth_vk_user_by_silent_token(silent_token, uuid)
    with SessionLocal() as db:
        user = db.query(User).filter(User.vk_id == vk_user.id).first()
        if user:
            user.vk_access_token = vk_user.access_token
            user.first_name = vk_user.first_name
            user.last_name = vk_user.last_name
            user.photo_url = vk_user.photo_url
            user.phone = vk_user.phone
            user.email = vk_user.email
            db.add(user)
        else:
            user = User(
                vk_id=vk_user.id,
                vk_access_token=vk_user.access_token,
                first_name=vk_user.first_name,
                last_name=vk_user.last_name,
                photo_url=vk_user.photo_url,
                phone=vk_user.phone,
                email=vk_user.email,
            )
            db.add(user)
        db.commit()
    return {'access_token': vk_user.access_token}


@app.post('/auth/firebase/', response_model=ResponseAuthSchema)
def auth_firebase(firebase_auth: RequestFirebaseAuthSchema):
    id_token = firebase_auth.id_token
    push_token = firebase_auth.push_token
    try:
        decoded_token = verify_id_token(id_token, app=get_firebase_app())
    except FirebaseError as ex:
        raise HTTPException(status_code=403, detail="Not authenticated")
    uid = decoded_token['uid']
    return {'access_token': 'some_secret_token', 'debug': f'Your uid = {uid}'}


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
def users_me(user: User = Depends(get_current_user)) -> PrivateUserSchema:
    return PrivateUserSchema.model_validate(user, from_attributes=True)
