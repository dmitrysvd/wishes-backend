from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from firebase_admin.auth import verify_id_token
from firebase_admin.exceptions import AlreadyExistsError, FirebaseError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import User
from app.dependencies import AUTH_TAG, get_current_user, get_db
from app.firebase import (
    create_custom_firebase_token,
    create_firebase_user,
    get_firebase_user_data,
)
from app.logging import logger
from app.schemas import (
    RequestFirebaseAuthSchema,
    RequestVkAuthMobileSchema,
    RequestVkAuthWebSchema,
    ResponseVkAuthMobileSchema,
    ResponseVkAuthWebSchema,
    SavePushTokenSchema,
)
from app.utils import new_user_handler, utc_now
from app.vk import (
    VkUserExtraData,
    exchange_tokens,
    get_vk_user_data_by_access_token,
    get_vk_user_friends,
)

router = APIRouter(tags=[AUTH_TAG])


def auth_vk(
    access_token: str,
    vk_extra_data: VkUserExtraData,
    db: Session,
) -> tuple[str, str, bool]:
    vk_basic_data = get_vk_user_data_by_access_token(access_token)

    user = db.scalars(select(User).where(User.vk_id == vk_basic_data.id)).one_or_none()
    if not user and vk_extra_data.email:
        user = db.scalars(
            select(User).where(User.email == vk_extra_data.email)
        ).one_or_none()

    is_new_user = not bool(user)
    if is_new_user:
        try:
            firebase_uid = create_firebase_user(
                email=vk_extra_data.email,
                display_name=f'{vk_basic_data.first_name} {vk_basic_data.last_name}',
                photo_url=vk_basic_data.photo_url,
                phone=vk_extra_data.phone,
            )
        except AlreadyExistsError as exc:
            logger.error('Ошибка при создании пользователя {exc}', exc=exc)
            raise HTTPException(
                409,
                'Пользователь с таким email уже существует. Зайдите через соответствующий аккаунт.',
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
    user.vk_id = str(vk_basic_data.id)
    if not user.vk_friends_data:
        user.vk_friends_data = get_vk_user_friends(access_token)
    user.last_login_at = utc_now()
    db.add(user)
    db.commit()

    if is_new_user:
        new_user_handler(user)

    firebase_token = create_custom_firebase_token(firebase_uid)
    return firebase_uid, firebase_token, is_new_user


@router.post('/auth/vk/web')
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
    firebase_uid, firebase_token, is_new_user = auth_vk(access_token, vk_extra_data, db)
    return ResponseVkAuthWebSchema(
        vk_access_token=access_token,
        firebase_uid=firebase_uid,
        firebase_token=firebase_token,
        user_created=is_new_user,
    )


@router.post('/auth/vk/mobile')
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
    firebase_uid, firebase_token, is_new_user = auth_vk(access_token, vk_extra_data, db)
    return ResponseVkAuthMobileSchema(
        firebase_uid=firebase_uid,
        firebase_token=firebase_token,
        user_created=is_new_user,
    )


@router.post('/auth/firebase', response_class=Response)
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

    user.last_login_at = utc_now()
    db.add(user)
    db.commit()

    if is_new_user:
        new_user_handler(user)


@router.post('/save_push_token', response_class=Response)
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
    user.firebase_push_token_saved_at = utc_now()
    db.add(user)
    db.commit()
