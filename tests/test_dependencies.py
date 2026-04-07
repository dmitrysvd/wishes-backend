from uuid import uuid4

import pytest
from fastapi import HTTPException, Request

from app.db import User, Wish
from app.dependencies import get_current_user, get_current_user_wish, get_db
from app.utils import utc_now


def test_get_db(db):
    gen = get_db()
    result = next(gen)
    assert result is not None
    try:
        next(gen)
    except StopIteration:
        pass


def test_get_current_user_no_token(db):
    request = Request(scope={'type': 'http', 'headers': []})
    with pytest.raises(HTTPException) as exc:
        get_current_user(request, db)
    assert exc.value.status_code == 401


def test_get_current_user_debug_token(db, mocker):
    mocker.patch('app.dependencies.settings.IS_DEBUG', True)
    mocker.patch('app.dependencies.settings.TEST_TOKEN', 'debug_token')

    user = User(
        display_name='Debug User', firebase_uid='debug_uid', registered_at=utc_now()
    )
    db.add(user)
    db.commit()

    token = f'debug_token:{user.id}'
    request = Request(
        scope={'type': 'http', 'headers': [(b'authorization', token.encode())]}
    )

    result = get_current_user(request, db)
    assert result.id == user.id


def test_get_current_user_debug_token_not_found(db, mocker):
    mocker.patch('app.dependencies.settings.IS_DEBUG', True)
    mocker.patch('app.dependencies.settings.TEST_TOKEN', 'debug_token')

    token = f'debug_token:{uuid4()}'
    request = Request(
        scope={'type': 'http', 'headers': [(b'authorization', token.encode())]}
    )

    with pytest.raises(HTTPException) as exc:
        get_current_user(request, db)
    assert exc.value.status_code == 401


def test_get_current_user_firebase_token_expired(db, mocker):
    from firebase_admin.auth import ExpiredIdTokenError

    mocker.patch('app.dependencies.settings.IS_DEBUG', False)
    mocker.patch(
        'app.dependencies.verify_id_token',
        side_effect=ExpiredIdTokenError('Expired', None),
    )

    request = Request(
        scope={'type': 'http', 'headers': [(b'authorization', b'expired_token')]}
    )

    with pytest.raises(HTTPException) as exc:
        get_current_user(request, db)
    assert exc.value.status_code == 401
    assert exc.value.detail == 'Token expired'


def test_get_current_user_firebase_token_invalid(db, mocker):
    from firebase_admin.auth import InvalidIdTokenError

    mocker.patch('app.dependencies.settings.IS_DEBUG', False)
    mocker.patch(
        'app.dependencies.verify_id_token', side_effect=InvalidIdTokenError('Invalid')
    )

    request = Request(
        scope={'type': 'http', 'headers': [(b'authorization', b'invalid_token')]}
    )

    with pytest.raises(HTTPException) as exc:
        get_current_user(request, db)
    assert exc.value.status_code == 401
    assert exc.value.detail == 'Invalid token'


def test_get_current_user_firebase_uid_not_found(db, mocker):
    mocker.patch('app.dependencies.settings.IS_DEBUG', False)
    mocker.patch(
        'app.dependencies.verify_id_token', return_value={'uid': 'non_existent_uid'}
    )

    request = Request(
        scope={'type': 'http', 'headers': [(b'authorization', b'valid_token')]}
    )

    with pytest.raises(HTTPException) as exc:
        get_current_user(request, db)
    assert exc.value.status_code == 401


def test_get_current_user_wish_not_found(db):
    user = User(display_name='User', firebase_uid='uid1', registered_at=utc_now())
    db.add(user)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        get_current_user_wish(uuid4(), user, db)
    assert exc.value.status_code == 404


def test_get_current_user_wish_forbidden(db):
    user = User(display_name='User', firebase_uid='uid1', registered_at=utc_now())
    other_user = User(
        display_name='Other User', firebase_uid='uid2', registered_at=utc_now()
    )
    db.add_all([user, other_user])
    db.commit()

    wish = Wish(name='Wish', user_id=other_user.id)
    db.add(wish)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        get_current_user_wish(wish.id, user, db)
    assert exc.value.status_code == 403


def test_get_current_user_wish_success(db):
    user = User(display_name='User', firebase_uid='uid1', registered_at=utc_now())
    db.add(user)
    db.commit()

    wish = Wish(name='Wish', user_id=user.id)
    db.add(wish)
    db.commit()

    result = get_current_user_wish(wish.id, user, db)
    assert result.id == wish.id
