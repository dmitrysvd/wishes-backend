from dataclasses import dataclass
from datetime import date, datetime
from typing import NoReturn

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError

from app.config import settings
from app.constants import Gender
from app.logging import logger

VK_API_VERSION = '5.191'
# VK ID (OAuth 2.1) — эндпоинт обмена authorization code на токены. Отличается от
# легаси api.vk.com/method/*: это OAuth-протокол, ответ плоский, ошибки в формате
# {'error', 'error_description'}. Токен, полученный тут серверным обменом,
# привязан к IP бэка (в отличие от Public Flow, где он привязан к IP телефона).
VK_ID_OAUTH_URL = 'https://id.vk.ru/oauth2/auth'


class VkResponseError(Exception):
    """VK вернул неразбираемый/неожиданный ответ.

    Это сбой интеграции (VK сменил формат, лёг или отдал мусор), а не ошибка
    аутентификации клиента. Намеренно НЕ `HTTPException`: должно долететь до
    `internal_exception_handler` и уйти в Hawk как 5xx с трейсом, а не быть
    проглоченным как чистый ответ.
    """


def _fail_unexpected_vk_response(
    method: str, body: object, cause: Exception | None = None
) -> NoReturn:
    """Залогировать неразбираемый ответ VK (с телом) и пробросить как 5xx → Hawk.

    ВНИМАНИЕ: тело может содержать перс.данные (email/phone). Пока это приемлемо —
    ответы редки и нужны для дебага; при ужесточении требований к PII тело нужно
    будет маскировать или логировать только структуру.
    """
    logger.error(
        'Неожиданный ответ VK {method}. Тело: {body}', method=method, body=body
    )
    raise VkResponseError(method) from cause


# VK кодирует пол числом: 1 — женский, 2 — мужской, 0 — не указан.
_VK_GENDER_MAP = {
    1: Gender.female,
    2: Gender.male,
}


def get_gender(vk_gender: int) -> Gender | None:
    """Преобразовать пол из кода VK. `0`/неизвестное значение → `None` (не указан)."""
    return _VK_GENDER_MAP.get(vk_gender)


def _parse_vk_birthdate(bdate: str | None) -> date | None:
    """Разобрать `bdate` из VK. Полная дата (`DD.MM.YYYY`) → `date`; скрытый год
    (`DD.MM`), пустое или неразбираемое значение → `None`."""
    if not bdate:
        return None
    try:
        return datetime.strptime(bdate, '%d.%m.%Y').date()
    except ValueError:
        # Юзер скрыл год (формат `DD.MM`) — год обязателен, иначе даты нет.
        return None


@dataclass(frozen=True)
class VkUserBasicData:
    """Поля, доступные для запроса в VK API по access_token."""

    id: int
    first_name: str
    last_name: str
    photo_url: str
    gender: Gender | None
    birthdate: date | None


@dataclass(frozen=True)
class VkUserExtraData:
    """
    Поля, которые нельзя запросить через VK API по access_token.

    Доступны только при аутентификации.
    """

    email: str | None
    phone: str | None


class _VkSilentAuthProfileSchema(BaseModel):
    """Профиль из auth.getProfileInfoBySilentToken. Форма VK строго не
    документирована, лишние поля не отбрасываем."""

    model_config = ConfigDict(extra='allow')

    email: str | None = None
    phone: str | None = None


class _VkSilentAuthResultSchema(BaseModel):
    errors: list[dict] = []
    success: list[_VkSilentAuthProfileSchema] = []


class _VkSilentAuthResponseSchema(BaseModel):
    response: _VkSilentAuthResultSchema


def get_extra_user_data_by_silent_token(
    silent_token: str, uuid: str
) -> VkUserExtraData:
    """Получить email/phone по silent-токену VK (верифицированный источник).

    Пока не используется: заготовка под мобильный VK ID SDK флоу, где email
    приходит подтверждённым от VK, а не из тела запроса клиента.
    """
    response = httpx.post(
        'https://api.vk.com/method/auth.getProfileInfoBySilentToken',
        params={
            'v': VK_API_VERSION,
            'access_token': settings.VK_SERVICE_KEY,
            'token': [silent_token],
            'uuid': [uuid],
            'event': [''],
        },
    )
    response.raise_for_status()
    response_json = response.json()
    try:
        parsed = _VkSilentAuthResponseSchema.model_validate(response_json)
    except ValidationError as exc:
        _fail_unexpected_vk_response('getProfileInfoBySilentToken', response_json, exc)
    # VK явно сообщил об ошибке — токен невалиден/протух, это ожидаемо.
    if parsed.response.errors:
        raise HTTPException(status_code=401, detail='Not authenticated')
    # Структура валидна, но профиля нет — так быть не должно, это сбой интеграции.
    if not parsed.response.success:
        _fail_unexpected_vk_response('getProfileInfoBySilentToken', response_json)
    profile = parsed.response.success[0]
    return VkUserExtraData(email=profile.email, phone=profile.phone)


class _VkUsersGetItemSchema(BaseModel):
    id: int
    first_name: str
    last_name: str
    photo_200: str
    # sex и bdate юзер может скрыть — считаем их опциональными.
    sex: int = 0
    bdate: str | None = None


class _VkUsersGetResponseSchema(BaseModel):
    response: list[_VkUsersGetItemSchema]


def get_vk_user_data_by_access_token(access_token: str) -> VkUserBasicData:
    response = httpx.get(
        'https://api.vk.com/method/users.get',
        params={
            'v': VK_API_VERSION,
            'access_token': access_token,
            'fields': 'photo_200, sex, bdate',
        },
    )
    response.raise_for_status()
    response_json = response.json()
    if 'error' in response_json:
        logger.debug('Ошибка авторизации vk: {text}', text=response_json['error'])
        raise HTTPException(status_code=401, detail='Not authenticated')
    try:
        parsed = _VkUsersGetResponseSchema.model_validate(response_json)
    except ValidationError as exc:
        _fail_unexpected_vk_response('users.get', response_json, exc)
    # Пустой список профилей при отсутствии `error` — неожиданно, сбой интеграции.
    if not parsed.response:
        _fail_unexpected_vk_response('users.get', response_json)
    user_data = parsed.response[0]
    return VkUserBasicData(
        id=user_data.id,
        first_name=user_data.first_name,
        last_name=user_data.last_name,
        photo_url=user_data.photo_200,
        gender=get_gender(user_data.sex),
        birthdate=_parse_vk_birthdate(user_data.bdate),
    )


class _VkFriendSchema(BaseModel):
    model_config = ConfigDict(extra='allow')

    id: int


class _VkFriendsGetResultSchema(BaseModel):
    items: list[_VkFriendSchema]


class _VkFriendsGetResponseSchema(BaseModel):
    response: _VkFriendsGetResultSchema


def get_vk_user_friends(access_token: str):
    response = httpx.get(
        'https://api.vk.com/method/friends.get',
        params={
            'v': VK_API_VERSION,
            'access_token': access_token,
            'order': 'hints',  # по рейтингу
            # нужно использовать любое поле, чтобы возвращались объекты, а не id-шники.
            'fields': 'bdate',
        },
    )
    response.raise_for_status()
    response_json = response.json()
    if 'error' in response_json:
        logger.debug('Ошибка авторизации vk: {text}', text=response_json['error'])
        raise HTTPException(status_code=401, detail='Not authenticated')
    try:
        parsed = _VkFriendsGetResponseSchema.model_validate(response_json)
    except ValidationError as exc:
        _fail_unexpected_vk_response('friends.get', response_json, exc)
    return [friend.model_dump() for friend in parsed.response.items]


class _VkIdTokenResponseSchema(BaseModel):
    """Ответ VK ID /oauth2/auth. OAuth 2.1 — поля на верхнем уровне (не под
    `response`). Лишние поля (refresh_token/id_token/expires_in/user_id/scope)
    не нужны для нашего флоу — отбрасываем."""

    model_config = ConfigDict(extra='ignore')

    access_token: str
    email: str | None = None
    phone: str | None = None


def exchange_vk_code(
    code: str, code_verifier: str, device_id: str
) -> tuple[str, VkUserExtraData]:
    """Обменять authorization code (VK ID Confidential Flow) на access_token.

    Обмен идёт на сервере с PKCE `code_verifier` от клиента — так VK ID привязывает
    выданный access_token к IP бэка, и последующие вызовы VK API проходят серверную
    валидацию (Public-Flow-токен, привязанный к IP телефона, серверу невалиден).

    Email/phone возвращает сам VK (подтверждённый источник) — их НЕ берём из тела
    запроса клиента (иначе возможен захват чужого аккаунта подстановкой email).
    """
    response = httpx.post(
        VK_ID_OAUTH_URL,
        data={
            'grant_type': 'authorization_code',
            'code': code,
            'code_verifier': code_verifier,
            'device_id': device_id,
            'client_id': settings.VK_APP_ID,
            'redirect_uri': settings.VK_REDIRECT_URI,
        },
    )
    response.raise_for_status()
    response_json = response.json()
    # VK ID сообщает об ошибке плоско: {'error': ..., 'error_description': ...}.
    # Это ожидаемо (код истёк/использован/невалиден) — отвечаем 401, не 5xx.
    # Логируем именно error/error_description (не тело — там может быть PII):
    # `error` однозначно указывает причину и нужен для диагностики прод-401
    # (invalid_client — не тот app id/secret; invalid_request+redirect_uri —
    # redirect не совпал; invalid_grant — код истёк/использован/не тот verifier).
    if 'error' in response_json:
        logger.warning(
            'VK ID отклонил обмен code: error={error} description={desc}',
            error=response_json.get('error'),
            desc=response_json.get('error_description'),
        )
        raise HTTPException(status_code=401, detail='Not authenticated')
    try:
        parsed = _VkIdTokenResponseSchema.model_validate(response_json)
    except ValidationError as exc:
        _fail_unexpected_vk_response('oauth2/auth', response_json, exc)
    vk_user_extra = VkUserExtraData(email=parsed.email, phone=parsed.phone)
    return parsed.access_token, vk_user_extra


class _VkExchangeTokenResultSchema(BaseModel):
    access_token: str
    email: str | None = None
    phone: str | None = None


class _VkExchangeTokenResponseSchema(BaseModel):
    response: _VkExchangeTokenResultSchema


def exchange_tokens(silent_token: str, uuid: str) -> tuple[str, VkUserExtraData]:
    response = httpx.post(
        'https://api.vk.com/method/auth.exchangeSilentAuthToken',
        data={
            'v': VK_API_VERSION,
            'token': silent_token,
            'access_token': settings.VK_SERVICE_KEY,
            'uuid': uuid,
        },
    )
    response.raise_for_status()
    response_json = response.json()
    if 'error' in response_json:
        logger.debug('Ошибка авторизации vk: {text}', text=response_json['error'])
        raise HTTPException(status_code=401, detail='Not authenticated')
    try:
        parsed = _VkExchangeTokenResponseSchema.model_validate(response_json)
    except ValidationError as exc:
        _fail_unexpected_vk_response('exchangeSilentAuthToken', response_json, exc)
    vk_user_extra = VkUserExtraData(
        phone=parsed.response.phone,
        email=parsed.response.email,
    )
    return parsed.response.access_token, vk_user_extra
