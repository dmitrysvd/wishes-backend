import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Boolean, ForeignKey, String, create_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)
from sqlalchemy.types import DECIMAL
from starlette.status import HTTP_401_UNAUTHORIZED

from schemas import AuthSchema, UserSchema, WishReadSchema, WishWriteSchema


@dataclass(frozen=True)
class ExtraUserData:
    first_name: str
    last_name: str
    photo_url: str


class Settings(BaseSettings):
    IS_DEBUG: bool
    VK_SERVICE_KEY: str
    VK_PROTECTED_KEY: str
    VK_APP_ID: int
    TEST_TOKEN: Optional[str] = None

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()  # type: ignore


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'user'

    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(30), nullable=False)
    last_name: Mapped[str] = mapped_column(String(30), nullable=False)
    vk_id: Mapped[str] = mapped_column(String(15), unique=True)
    photo_url: Mapped[str] = mapped_column(String(200))
    vk_access_token: Mapped[str] = mapped_column(String(100), unique=True)

    wishes: Mapped[list['Wish']] = relationship(
        back_populates='user', cascade='all, delete-orphan'
    )


class Wish(Base):
    __tablename__ = 'wish'

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user.id'))
    name: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(String(1000), nullable=True)
    price: Mapped[Decimal] = mapped_column(DECIMAL(precision=2), nullable=True)
    is_active: Mapped[Boolean] = mapped_column(Boolean(), default=False)

    user: Mapped['User'] = relationship(back_populates='wishes')


BASE_DIR = Path(__file__).parent

engine = create_engine(
    'sqlite:///db.sqlite',
    echo=settings.IS_DEBUG,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()
templates = Jinja2Templates(directory="templates")
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


def get_user_data_by_silent_token(silent_token: str, uuid: str) -> ExtraUserData:
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
    return ExtraUserData(
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


@app.get('/auth-vk-complete/', response_model=AuthSchema)
def auth_vk_complete(request: Request):
    payload = request.query_params['payload']
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


@app.get('/auth-vk/')
def auth_vk(request: Request):
    return templates.TemplateResponse(
        "registration.html",
        {
            "request": request,
            "vk_app_id": settings.VK_APP_ID,
            "auth_redirect_url": 'https://hotelki.pro/auth-vk-complete/',
        },
    )


@app.get('/')
def main(request: Request):
    try:
        get_current_user(request, db=SessionLocal())
    except HTTPException:
        return RedirectResponse('/auth-vk')
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
