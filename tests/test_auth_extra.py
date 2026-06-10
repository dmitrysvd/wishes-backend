from datetime import date

import pytest
from fastapi import HTTPException
from firebase_admin.exceptions import AlreadyExistsError, FirebaseError

from app.constants import Gender
from app.routers.auth import auth_firebase, auth_vk
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


def test_auth_firebase_invalid_token(mocker, db):
    mocker.patch(
        'app.routers.auth.verify_id_token', side_effect=FirebaseError(1, 'error')
    )
    from app.schemas import RequestFirebaseAuthSchema

    with pytest.raises(HTTPException) as exc:
        auth_firebase(RequestFirebaseAuthSchema(id_token='bad'), db)
    assert exc.value.status_code == 403
