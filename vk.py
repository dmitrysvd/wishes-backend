import enum
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from fastapi import HTTPException

from config import settings

VK_API_VERSION = '5.191'


class Gender(enum.Enum):
    male = enum.auto()
    female = enum.auto()


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


@dataclass(frozen=True)
class VkUserExtraData:
    """
    Поля, которые нельзя запросить через VK API по access_token.

    Доступны только при аутентификации.
    """

    email: str
    phone: Optional[str]


def get_extra_user_data_by_silent_token(
    silent_token: str, uuid: str
) -> VkUserExtraData:
    response = httpx.post(
        'https://api.vk.com/method/auth.getProfileInfoBySilentToken',
        params={
            "v": VK_API_VERSION,
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
    user_data = response_json['response']['success'][0]
    return user_data


def get_vk_user_data_by_access_token(access_token: str) -> VkUserBasicData:
    response = httpx.get(
        'https://api.vk.com/method/users.get',
        params={
            'v': VK_API_VERSION,
            'access_token': access_token,
            'fields': 'photo_200, sex',
        },
    )
    user_data = response.json()['response'][0]
    return VkUserBasicData(
        id=user_data['id'],
        first_name=user_data['first_name'],
        last_name=user_data['last_name'],
        photo_url=user_data['photo_200'],
        gender=get_gender(user_data['sex']),
    )


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
        raise HTTPException(status_code=401, detail="Not authenticated")
    response.raise_for_status()
    response_json = response.json()
    if 'error' in response_json:
        raise HTTPException(status_code=401, detail="Not authenticated")
    exchange_token_response = response_json['response']
    access_token = exchange_token_response['access_token']
    vk_user_extra = VkUserExtraData(
        phone=exchange_token_response.get('phone'),
        email=exchange_token_response['email'],
    )
    return access_token, vk_user_extra


def auth_vk_user_by_silent_token(silent_token: str, uuid: str) -> VkUserBasicData:
    # Deprecated
    return VkUserBasicData(
        id=exchange_token_response['user_id'],
        access_token=exchange_token_response['access_token'],
        photo_url=user_data_by_silent_token['photo_200'],
        first_name=user_data_by_silent_token['first_name'],
        last_name=user_data_by_silent_token['last_name'],
        phone='79991122345',  # Тестовый номер
        email=exchange_token_response['email'],
    )
