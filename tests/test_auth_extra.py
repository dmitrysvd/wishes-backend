from datetime import date

import pytest
from fastapi import HTTPException
from firebase_admin.exceptions import AlreadyExistsError, FirebaseError

from app.constants import Gender
from app.db import User
from app.routers.auth import auth_firebase, auth_vk
from app.utils import utc_now
from app.vk import VkUserBasicData, VkUserExtraData


def test_auth_vk_already_exists(mocker, db):
    mocker.patch(
        'app.routers.auth.get_vk_user_data_by_access_token',
        return_value=VkUserBasicData(
            id=1,
            first_name='A',
            last_name='B',
            photo_url='',
            gender=Gender.male,
            birthdate=date(1990, 1, 1),
        ),
    )
    mocker.patch(
        'app.routers.auth.create_firebase_user',
        side_effect=AlreadyExistsError('exists'),
    )

    with pytest.raises(HTTPException) as exc:
        auth_vk('token', VkUserExtraData(email='exists@test.com', phone=None), db)
    assert exc.value.status_code == 409


def test_auth_vk_keeps_friends_snapshot_when_refresh_fails(mocker, db):
    # Обновление снимка VK-друзей best-effort: сбой VK-запроса не валит логин и
    # оставляет прежний снимок (камень 2 — было one-shot, стало refresh-on-login).
    existing = User(
        display_name='Existing',
        vk_id='42',
        vk_friends_data=[{'id': 'keep'}],
        firebase_uid='fb_42',
        registered_at=utc_now(),
    )
    db.add(existing)
    db.commit()

    mocker.patch(
        'app.routers.auth.get_vk_user_data_by_access_token',
        return_value=VkUserBasicData(
            id=42,
            first_name='A',
            last_name='B',
            photo_url='',
            gender=Gender.male,
            birthdate=None,
        ),
    )
    mocker.patch(
        'app.routers.auth.get_vk_user_friends',
        side_effect=Exception('VK down'),
    )
    mocker.patch('app.routers.auth.create_custom_firebase_token', return_value='tok')

    _, _, is_new = auth_vk('token', VkUserExtraData(email=None, phone=None), db)
    assert is_new is False
    db.refresh(existing)
    assert existing.vk_friends_data == [{'id': 'keep'}]


def test_auth_vk_refreshes_friends_snapshot_on_login(mocker, db):
    # На успешном входе снимок VK-друзей перезаписывается свежими данными.
    existing = User(
        display_name='Existing',
        vk_id='43',
        vk_friends_data=[{'id': 'old'}],
        firebase_uid='fb_43',
        registered_at=utc_now(),
    )
    db.add(existing)
    db.commit()

    mocker.patch(
        'app.routers.auth.get_vk_user_data_by_access_token',
        return_value=VkUserBasicData(
            id=43,
            first_name='A',
            last_name='B',
            photo_url='',
            gender=Gender.male,
            birthdate=None,
        ),
    )
    mocker.patch(
        'app.routers.auth.get_vk_user_friends',
        return_value=[{'id': 'fresh'}],
    )
    mocker.patch('app.routers.auth.create_custom_firebase_token', return_value='tok')

    auth_vk('token', VkUserExtraData(email=None, phone=None), db)
    db.refresh(existing)
    assert existing.vk_friends_data == [{'id': 'fresh'}]


def test_auth_firebase_invalid_token(mocker, db):
    mocker.patch(
        'app.routers.auth.verify_id_token', side_effect=FirebaseError(1, 'error')
    )
    from app.schemas import RequestFirebaseAuthSchema

    with pytest.raises(HTTPException) as exc:
        auth_firebase(RequestFirebaseAuthSchema(id_token='bad'), db)
    assert exc.value.status_code == 403
