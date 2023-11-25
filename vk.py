from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException

from config import settings

VK_API_VERSION = '5.191'


@dataclass(frozen=True)
class VkUserData:
    id: int
    first_name: str
    last_name: str
    photo_url: str
    access_token: str
    phone: str
    email: str


def get_user_data_by_silent_token(silent_token: str, uuid: str) -> dict[str, Any]:
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


def get_user_data(vk_id: str, access_token: str):
    response = httpx.get(
        'https://api.vk.com/method/users.get',
        params={
            'v': VK_API_VERSION,
            'access_token': settings.VK_SERVICE_KEY,
            'user_ids': [vk_id],
        },
    )
    data = response.json()
    print()
    return data


def exchange_tokens(silent_token: str, uuid: str) -> dict[str, Any]:
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
    return response_json['response']


def auth_vk_user_by_silent_token(silent_token: str, uuid: str) -> VkUserData:
    user_data_by_silent_token = get_user_data_by_silent_token(silent_token, uuid)
    exchange_token_response = exchange_tokens(silent_token, uuid)
    return VkUserData(
        id=exchange_token_response['user_id'],
        access_token=exchange_token_response['access_token'],
        photo_url=user_data_by_silent_token['photo_200'],
        first_name=user_data_by_silent_token['first_name'],
        last_name=user_data_by_silent_token['last_name'],
        phone='79991122345',  # Тестовый номер
        email=exchange_token_response['email'],
    )
