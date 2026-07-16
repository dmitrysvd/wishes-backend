from dataclasses import dataclass
from datetime import date, datetime

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError

from app.config import settings
from app.constants import Gender
from app.logging import logger

VK_API_VERSION = '5.191'

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
    except ValidationError:
        raise HTTPException(status_code=401, detail='Not authenticated') from None
    if parsed.response.errors or not parsed.response.success:
        raise HTTPException(status_code=401, detail='Not authenticated')
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
    except ValidationError:
        raise HTTPException(status_code=401, detail='Not authenticated') from None
    if not parsed.response:
        raise HTTPException(status_code=401, detail='Not authenticated')
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
    try:
        parsed = _VkFriendsGetResponseSchema.model_validate(response_json)
    except ValidationError:
        raise HTTPException(status_code=401, detail='Not authenticated') from None
    return [friend.model_dump() for friend in parsed.response.items]


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
    except ValidationError:
        raise HTTPException(status_code=401, detail='Not authenticated') from None
    vk_user_extra = VkUserExtraData(
        phone=parsed.response.phone,
        email=parsed.response.email,
    )
    return parsed.response.access_token, vk_user_extra
