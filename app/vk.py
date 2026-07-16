from dataclasses import dataclass
from datetime import date, datetime

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import settings
from app.constants import Gender
from app.logging import logger

VK_API_VERSION = '5.191'


def get_gender(vk_gender: int) -> Gender:
    return {
        1: Gender.female,
        2: Gender.male,
    }[vk_gender]


@dataclass(frozen=True)
class VkUserBasicData:
    """Поля, доступные для запроса в VK API по access_token."""

    id: int
    first_name: str
    last_name: str
    photo_url: str
    gender: Gender
    birthdate: date


@dataclass(frozen=True)
class VkUserExtraData:
    """
    Поля, которые нельзя запросить через VK API по access_token.

    Доступны только при аутентификации.
    """

    email: str | None
    phone: str | None


class _VkSilentAuthProfileSchema(BaseModel):
    """Поля профиля из auth.getProfileInfoBySilentToken. Форма VK не документирована
    строго, поэтому лишние поля не отбрасываем."""

    model_config = ConfigDict(extra='allow')


class _VkSilentAuthResultSchema(BaseModel):
    errors: list[dict] = Field(default_factory=list)
    success: list[_VkSilentAuthProfileSchema] = Field(default_factory=list)


class _VkSilentAuthResponseSchema(BaseModel):
    response: _VkSilentAuthResultSchema


def get_extra_user_data_by_silent_token(
    silent_token: str, uuid: str
) -> VkUserExtraData:
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
    if response_json.get('response', {}).get('errors'):
        raise HTTPException(status_code=401, detail='Not authenticated')
    try:
        parsed = _VkSilentAuthResponseSchema.model_validate(response_json)
    except ValidationError:
        raise HTTPException(status_code=401, detail='Not authenticated') from None
    if not parsed.response.success:
        raise HTTPException(status_code=401, detail='Not authenticated')
    return parsed.response.success[0].model_dump()


class _VkUsersGetItemSchema(BaseModel):
    id: int
    first_name: str
    last_name: str
    photo_200: str
    sex: int
    bdate: str


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
    birthdate = datetime.strptime(user_data.bdate, '%d.%m.%Y').date()
    return VkUserBasicData(
        id=user_data.id,
        first_name=user_data.first_name,
        last_name=user_data.last_name,
        photo_url=user_data.photo_200,
        gender=get_gender(user_data.sex),
        birthdate=birthdate,
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
