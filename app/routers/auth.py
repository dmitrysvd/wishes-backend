from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from firebase_admin.auth import verify_id_token
from firebase_admin.exceptions import AlreadyExistsError, FirebaseError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.status import HTTP_410_GONE

from app.db import User
from app.dependencies import AUTH_TAG, get_current_user, get_db
from app.firebase import (
    create_custom_firebase_token,
    create_firebase_user,
    get_firebase_user_data,
)
from app.helpers import refresh_avatar_on_login
from app.logging import logger
from app.push_installations import upsert_push_installation
from app.schemas import (
    AuthFirebaseResponseSchema,
    RegistrationAttributionSchema,
    RequestFirebaseAuthSchema,
    RequestVkAuthVkidSchema,
    ResponseVkAuthMobileSchema,
    SavePushTokenSchema,
)
from app.utils import (
    create_invite_mutual_follow,
    new_user_handler,
    save_registration_attribution,
    utc_now,
)
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


# Регистрация по инвайту (фича 0024): поведение клиента после входа и пуш
# пригласившему. Структурно, для аудитора и кодгена фронта (PROTOCOL.md §7);
# общее у обеих живых auth-ручек.
_INVITE_WORKFLOW = [
    'Ответ с `mutual_follow_user_id != null`: новичок и этот юзер подписаны друг '
    'на друга с момента регистрации. Когда новичок на user_page (S5) этого юзера '
    '(обычно сразу: маршрут deep link переживает логин), один раз показать '
    'ненавязчивую плашку «Вы с {display_name} подписаны друг на друга: напомним о '
    'дне рождения»; имя — из `GET /users/{mutual_follow_user_id}`. «Один раз» '
    'помнит клиент; повторно ответ с этим id не придёт — поле ненулевое только '
    'в ответе, создавшем аккаунт.',
    '`mutual_follow_user_id == null` — плашки нет, ничего не делать.',
    'Кнопка подписки на S5 пригласившего берёт состояние из свежего профиля '
    '(`followed_by_me == true` — «вы подписаны»); CTA-блок подписки при этом не '
    'показывается (см. `x-workflow` у `GET /users/{user_id}`).',
    'Пригласившему бэк сам шлёт пуш «{имя} присоединился по вашей ссылке», '
    'payload — `x-push-payload` этой операции. Он заменяет пуш «новый подписчик» '
    'для этого ребра и подчиняется группе «Друзья» настроек уведомлений.',
]

# Пуш пригласившему о регистрации по его ссылке (фича 0024). Формат как у
# остальных пушей: `notification` рисует ОС, `data` — роутинг и тост в foreground.
_INVITE_JOINED_PUSH_PAYLOAD = {
    'kind': 'invite_joined',
    'notification': {
        'title': 'Анна присоединилась по вашей ссылке',
        'body': 'Теперь вы подписаны друг на друга',
    },
    'data': {
        'click_action': 'FLUTTER_NOTIFICATION_CLICK',
        'type': 'invite_joined',
        'delivery_id': '5c1c9a2e-7b1d-4e3a-9f0a-2d6b8c4e1a77',
        'link': (
            'https://hotelki.pro/user'
            '?userId=9b2d5e4a-1c3f-4a2b-8d6e-0f1a2b3c4d5e&via=push#'
        ),
        'title': 'Анна присоединилась по вашей ссылке',
        'body': 'Теперь вы подписаны друг на друга',
    },
    'fields': {
        'type': 'Вид пуша, `invite_joined`; клиенту для роутинга не нужен.',
        'link': (
            'Профиль новичка (S5): `{FRONTEND_URL}/user?userId=<uuid>&via=push#`. '
            'Открывать через роутер, как остальные пуши. `via=push` — маркер '
            '«открыт из пуша»: CTA-блок подписки не показывается, подписка с этого '
            'профиля идёт с `source=push`.'
        ),
        'delivery_id': (
            'UUID строки лога отправки — тело `POST /push/opened` при открытии.'
        ),
    },
    'texts': {
        'title': '{display_name} присоединился по вашей ссылке',
        'body': 'Теперь вы подписаны друг на друга',
        'rules': (
            '«присоединилась» — если у новичка `gender = female`, иначе '
            '«присоединился». Имя — `display_name` новичка как есть.'
        ),
    },
}

_INVITE_OPENAPI_EXTRA: dict[str, Any] = {
    'x-workflow': _INVITE_WORKFLOW,
    'x-push-payload': _INVITE_JOINED_PUSH_PAYLOAD,
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
    firebase_uid, firebase_token, is_new_user, mutual_follow_user_id = auth_vk(
        access_token, vk_extra_data, db, request_data.attribution
    )
    return ResponseVkAuthMobileSchema(
        firebase_uid=firebase_uid,
        firebase_token=firebase_token,
        user_created=is_new_user,
        mutual_follow_user_id=mutual_follow_user_id,
    )


def auth_vk(
    access_token: str,
    vk_extra_data: VkUserExtraData,
    db: Session,
    attribution: RegistrationAttributionSchema | None = None,
) -> tuple[str, str, bool, UUID | None]:
    """Завести/найти юзера по VK-профилю и выдать firebase custom token.

    `access_token` и `vk_extra_data` — только из серверного обмена VK ID
    (`exchange_vk_code`): токен выпущен под наше приложение, email подтверждён VK.
    Принимать сюда токен/email из тела клиента нельзя — токен чужого VK-приложения
    даёт вход под чужим vk_id, а неподтверждённый email — вход в чужой аккаунт.
    """
    vk_basic_data = get_vk_user_data_by_access_token(access_token)

    user = db.scalars(
        select(User).where(User.vk_id == str(vk_basic_data.id))
    ).one_or_none()
    # Email подтверждён VK (серверный обмен) — можно связать с существующим
    # аккаунтом, заведённым через Google/Firebase.
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
    # Снимок VK-друзей обновляем на КАЖДОМ входе (раньше был one-shot и протухал:
    # новые друзья не попадали, ушедшие оставались — бьёт по бёрздей-радару и
    # possible_friends). Best-effort: сбой VK-запроса не должен ронять логин —
    # оставляем прежний снимок.
    try:
        user.vk_friends_data = get_vk_user_friends(access_token)
    except Exception as exc:
        logger.warning('Не удалось обновить список VK-друзей: {exc}', exc=exc)
    user.last_login_at = utc_now()
    db.add(user)
    db.commit()

    # Свежую соц-аватарку перекачиваем на диск (best-effort). Хотлинк на VK-CDN в
    # photo_url не сохраняем — только своя /media. См. refresh_avatar_on_login.
    refresh_avatar_on_login(user, vk_basic_data.photo_url, db)

    mutual_follow_user_id = None
    if is_new_user:
        new_user_handler(user)
        # first-touch атрибуция и взаимные подписки по инвайту — только для
        # нового юзера, best-effort
        save_registration_attribution(db, user, attribution)
        mutual_follow_user_id = create_invite_mutual_follow(db, user, attribution)

    firebase_token = create_custom_firebase_token(firebase_uid)
    return firebase_uid, firebase_token, is_new_user, mutual_follow_user_id


LEGACY_VK_MOBILE_GONE_DETAIL = (
    'Обновите приложение: этот способ входа больше не поддерживается'
)


@router.post(
    '/auth/vk/mobile',
    deprecated=True,
    # Публичный путь: у старого клиента токена нет — иначе он получил бы 401 до 410.
    openapi_extra={'security': []},
    status_code=HTTP_410_GONE,
    responses={
        HTTP_410_GONE: {
            'description': (
                'Всегда. Легаси-вход (Public Flow, access_token и email из тела) '
                'удалён как уязвимый; заглушка отдаёт только сообщение об '
                'обновлении — старые сборки (≤ 1.1.14) показывают `detail` тостом. '
                'Тело запроса не читается и не валидируется.'
            ),
            'content': {
                'application/json': {
                    'example': {'detail': LEGACY_VK_MOBILE_GONE_DETAIL}
                }
            },
        },
    },
)
def auth_vk_mobile_gone() -> None:
    """Легаси, только сообщение об обновлении. Живой VK-вход — `POST /auth/vk/vkid`."""
    raise HTTPException(HTTP_410_GONE, LEGACY_VK_MOBILE_GONE_DETAIL)


@router.post(
    '/auth/vk/vkid',
    # Публичный вход: токена у клиента ещё нет — снимаем глобальное требование ApiKey.
    openapi_extra={'security': [], **_INVITE_OPENAPI_EXTRA},
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

    Сайд-эффект (регистрация по инвайту, фича 0024): если юзер создаётся впервые и
    `attribution.referrer_id` принят (см. `RegistrationAttributionSchema`), новичок
    и пригласивший сразу подписаны друг на друга — без подтверждения; обычная
    отписка работает как для любой подписки. Итог — `mutual_follow_user_id` в
    ответе. Best-effort: подписки не создались — регистрация всё равно успешна,
    поле `null`. Пригласившему уходит пуш — `x-push-payload`; поведение клиента
    после входа — `x-workflow` (Swagger UI расширений не показывает — читайте спек).
    """
    return auth_vk_via_code(request_data, db)


@router.post(
    '/auth/firebase',
    # Публичный вход: Firebase ID-токен приходит в теле, а не в `Authorization`.
    openapi_extra={'security': [], **_INVITE_OPENAPI_EXTRA},
    responses={
        403: {
            'description': (
                'Firebase отклонил `id_token`: битый, истёк или выпущен не нашим '
                'проектом. Нужен новый вход в Firebase и повтор.'
            ),
            'content': {
                'application/json': {'example': {'detail': 'Not authenticated'}}
            },
        },
    },
)
def auth_firebase(
    firebase_auth_schema: RequestFirebaseAuthSchema,
    db: Session = Depends(get_db),
) -> AuthFirebaseResponseSchema:
    """
    Аутентификация через Firebase (Google).

    Клиент уже должен быть залогинен в Firebase и передаёт его ID-токен.
    Если пользователя с этим Firebase-аккаунтом (или подтверждённым email) нет,
    создаёт его (`user_created = true`); иначе — вход в существующий аккаунт.

    Сайд-эффект (атрибуция): если передан `attribution` и юзер создаётся впервые,
    бэк фиксирует реферера и канал установки (см. `RegistrationAttributionSchema`).
    Best-effort: невалидная атрибуция тихо игнорируется, регистрацию не валит. Для
    существующего юзера атрибуция игнорируется (first-touch).

    Сайд-эффект (регистрация по инвайту, фича 0024): если юзер создаётся впервые и
    `attribution.referrer_id` принят (см. `RegistrationAttributionSchema`), новичок
    и пригласивший сразу подписаны друг на друга — без подтверждения; обычная
    отписка работает как для любой подписки. Итог — `mutual_follow_user_id` в
    ответе. Best-effort: подписки не создались — регистрация всё равно успешна,
    поле `null`. Пригласившему уходит пуш — `x-push-payload`; поведение клиента
    после входа — `x-workflow` (Swagger UI расширений не показывает — читайте спек).
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

    mutual_follow_user_id = None
    if is_new_user:
        new_user_handler(user)
        # first-touch атрибуция и взаимные подписки по инвайту — только для
        # нового юзера, best-effort
        attribution = firebase_auth_schema.attribution
        save_registration_attribution(db, user, attribution)
        mutual_follow_user_id = create_invite_mutual_follow(db, user, attribution)

    return AuthFirebaseResponseSchema(
        user_created=is_new_user, mutual_follow_user_id=mutual_follow_user_id
    )


@router.post(
    '/save_push_token',
    response_class=Response,
    responses={200: {'description': 'Установка сохранена. Тело пустое.'}},
)
def save_push_token(
    schema: SavePushTokenSchema,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """
    Сохранить адреса установки приложения: FCM-токен и, у нового клиента, FID
    (фича 0016). Семантика установок — в `SavePushTokenSchema`.

    Вызывается на каждом логине и при рефреше адреса на foreground (0008).
    Конфликтов не бывает — ответ всегда `200`.
    """
    upsert_push_installation(db, user, fid=schema.fid, push_token=schema.push_token)
    db.commit()
