from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from firebase_admin.auth import verify_id_token
from firebase_admin.exceptions import AlreadyExistsError, FirebaseError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import User
from app.dependencies import AUTH_TAG, get_current_user, get_db
from app.firebase import (
    create_custom_firebase_token,
    create_firebase_user,
    get_firebase_user_data,
)
from app.helpers import refresh_avatar_on_login
from app.logging import logger
from app.schemas import (
    RegistrationAttributionSchema,
    RequestFirebaseAuthSchema,
    RequestVkAuthMobileSchema,
    RequestVkAuthVkidSchema,
    ResponseVkAuthMobileSchema,
    SavePushTokenSchema,
)
from app.utils import new_user_handler, save_registration_attribution, utc_now
from app.vk import (
    VkUserExtraData,
    exchange_vk_code,
    get_vk_user_data_by_access_token,
    get_vk_user_friends,
)

router = APIRouter(tags=[AUTH_TAG])

# Коды ответов для VK ID Confidential Flow (обмен `code` на сервере) — /auth/vk/vkid.
_VK_CODE_AUTH_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {
        'description': (
            'VK ID отклонил обмен `code`: код невалиден, истёк, уже использован, '
            'либо `code_verifier`/`device_id`/`redirect_uri` не совпали. Повторять '
            'с тем же `code` бессмысленно — нужен новый вход.'
        ),
        'content': {'application/json': {'example': {'detail': 'Not authenticated'}}},
    },
    409: {
        'description': (
            'Email из подтверждённого профиля VK уже занят другим аккаунтом '
            '(например, регистрация была через Google). Нужно войти через '
            'соответствующий аккаунт.'
        ),
        'content': {
            'application/json': {
                'example': {
                    'detail': (
                        'Пользователь с таким email уже существует. '
                        'Зайдите через соответствующий аккаунт.'
                    )
                }
            }
        },
    },
}


def auth_vk_via_code(
    request_data: RequestVkAuthVkidSchema,
    db: Session,
) -> ResponseVkAuthMobileSchema:
    """Обмен VK ID authorization `code` на сессию (Confidential Flow).

    Логика `/auth/vk/vkid`: серверный обмен `code` на access_token у VK ID Backend
    (токен привязан к IP бэка), подтверждённый профиль (в т.ч. email) берётся из
    `id_token`, а не из тела.
    """
    access_token, vk_extra_data = exchange_vk_code(
        request_data.code,
        request_data.code_verifier,
        request_data.device_id,
        request_data.redirect_uri,
    )
    # email из серверного обмена VK ID — подтверждён VK (не из тела клиента).
    firebase_uid, firebase_token, is_new_user = auth_vk(
        access_token, vk_extra_data, db, request_data.attribution, email_verified=True
    )
    return ResponseVkAuthMobileSchema(
        firebase_uid=firebase_uid,
        firebase_token=firebase_token,
        user_created=is_new_user,
    )


def auth_vk(
    access_token: str,
    vk_extra_data: VkUserExtraData,
    db: Session,
    attribution: RegistrationAttributionSchema | None = None,
    email_verified: bool = False,
) -> tuple[str, str, bool]:
    vk_basic_data = get_vk_user_data_by_access_token(access_token)

    user = db.scalars(
        select(User).where(User.vk_id == str(vk_basic_data.id))
    ).one_or_none()
    # Связывать VK-вход с существующим аккаунтом по email можно ТОЛЬКО если email
    # подтверждён VK (серверный обмен). Иначе (легаси-mobile: email из тела клиента)
    # подстановка чужого email дала бы вход в чужой аккаунт — по email не матчим.
    if not user and email_verified and vk_extra_data.email:
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
                (
                    'Пользователь с таким email уже существует. '
                    'Зайдите через соответствующий аккаунт.'
                ),
            ) from None
        user = User(
            vk_id=vk_basic_data.id,
            vk_access_token=access_token,
            display_name=f'{vk_basic_data.first_name} {vk_basic_data.last_name}',
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

    # Свежую соц-аватарку перекачиваем на диск (best-effort). Хотлинк на VK-CDN в
    # photo_url не сохраняем — только своя /media. См. refresh_avatar_on_login.
    refresh_avatar_on_login(user, vk_basic_data.photo_url, db)

    if is_new_user:
        new_user_handler(user)
        # first-touch атрибуция — только для нового юзера, best-effort
        save_registration_attribution(db, user, attribution)

    firebase_token = create_custom_firebase_token(firebase_uid)
    return firebase_uid, firebase_token, is_new_user


@router.post('/auth/vk/mobile', deprecated=True)
def auth_vk_mobile(
    auth_data: RequestVkAuthMobileSchema, db: Session = Depends(get_db)
) -> ResponseVkAuthMobileSchema:
    """
    **DEPRECATED — используйте `POST /auth/vk/vkid`.**

    Легаси-вход через ВК на мобильных устройствах (Public Flow): клиент присылает
    готовый `access_token`, а email/phone — в теле запроса (не подтверждены VK,
    поэтому вход по email с существующим аккаунтом не связывается). Оставлен ради
    уже зашипленных старых мобильных клиентов; новые интеграции — на `/auth/vk/vkid`
    (Confidential Flow, подтверждённый профиль из `id_token`). Снимется, когда старые
    клиенты переедут.

    Создаст пользователя в firebase и на сервере, если не существовал.
    Возвращает данные для аутентификации в firebase.

    Сайд-эффект (атрибуция): если передан `attribution` и юзер создаётся впервые
    (`user_created=true`), бэк фиксирует реферера и канал установки (см.
    `RegistrationAttributionSchema`). Best-effort: невалидная атрибуция тихо
    игнорируется, регистрацию не валит. Для существующего юзера атрибуция
    игнорируется (first-touch).
    """
    access_token = auth_data.access_token
    vk_extra_data = VkUserExtraData(email=auth_data.email, phone=auth_data.phone)
    firebase_uid, firebase_token, is_new_user = auth_vk(
        access_token, vk_extra_data, db, auth_data.attribution
    )
    return ResponseVkAuthMobileSchema(
        firebase_uid=firebase_uid,
        firebase_token=firebase_token,
        user_created=is_new_user,
    )


@router.post(
    '/auth/vk/vkid',
    # Публичный вход: токена у клиента ещё нет — снимаем глобальное требование ApiKey.
    openapi_extra={'security': []},
    responses=_VK_CODE_AUTH_RESPONSES,
)
def auth_vk_vkid(
    request_data: RequestVkAuthVkidSchema,
    db: Session = Depends(get_db),
) -> ResponseVkAuthMobileSchema:
    """
    Аутентификация через VK ID (Confidential Flow, OAuth 2.1) — единый вход веб+мобилки.

    Клиент (веб-виджет One Tap `@vkid/sdk` или нативный SDK) присылает authorization
    `code` (+ `code_verifier`, `device_id`, `redirect_uri`); бэк меняет его на токены
    у VK ID Backend (обмен на стороне сервера, токен привязан к IP бэка), берёт
    подтверждённый профиль (в т.ч. email) из `id_token` и заводит/находит юзера.
    Создаст пользователя в firebase и на сервере, если не существовал. Возвращает
    данные для аутентификации в firebase (`signInWithCustomToken`).

    Недоступность VK ID / таймаут обмена — это `5xx` (вне контракта): фронт
    фолбэчит генерик-ошибкой «попробуйте позже», отдельной семантики у тела нет.

    Сайд-эффект (атрибуция): если передан `attribution` и юзер создаётся впервые
    (`user_created=true`), бэк фиксирует реферера и канал установки (см.
    `RegistrationAttributionSchema`). Best-effort: невалидная атрибуция тихо
    игнорируется, регистрацию не валит. Для существующего юзера атрибуция
    игнорируется (first-touch).
    """
    return auth_vk_via_code(request_data, db)


@router.post('/auth/firebase', response_class=Response)
def auth_firebase(
    firebase_auth_schema: RequestFirebaseAuthSchema,
    db: Session = Depends(get_db),
):
    """
    Аутентификация через firebase Google trololo.

    Клиент уже должен быть залогинен в firebase.
    Если пользователя с email из firebase нет в БД, создаст его.
    Если пользователь уже есть, ничего не делает.

    Сайд-эффект (атрибуция): если передан `attribution` и юзер создаётся впервые,
    бэк фиксирует реферера и канал установки (см. `RegistrationAttributionSchema`).
    Best-effort: невалидная атрибуция тихо игнорируется, регистрацию не валит. Для
    существующего юзера атрибуция игнорируется (first-touch).
    """
    id_token = firebase_auth_schema.id_token
    try:
        decoded_token = verify_id_token(id_token)
    except FirebaseError:
        raise HTTPException(status_code=403, detail='Not authenticated') from None
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

    # Свежую соц-аватарку (Google) перекачиваем на диск в высоком разрешении.
    refresh_avatar_on_login(user, firebase_user.photo_url, db)

    if is_new_user:
        new_user_handler(user)
        # first-touch атрибуция — только для нового юзера, best-effort
        save_registration_attribution(db, user, firebase_auth_schema.attribution)


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
