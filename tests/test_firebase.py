from datetime import datetime
from unittest.mock import MagicMock
from uuid import uuid4

from firebase_admin import messaging
from sqlalchemy import select

from app.db import User
from app.firebase import (
    create_custom_firebase_token,
    create_firebase_user,
    dead_token_user_ids,
    delete_firebase_user,
    get_firebase_user_data,
    send_push,
)


class FakeSendResponse:
    """Лёгкая замена firebase_admin messaging.SendResponse для тестов."""

    def __init__(self, success, exception=None):
        self.success = success
        self.exception = exception


class FakeBatchResponse:
    """Лёгкая замена messaging.BatchResponse для тестов."""

    def __init__(self, responses):
        self.responses = responses
        self.success_count = sum(1 for r in responses if r.success)
        self.failure_count = sum(1 for r in responses if not r.success)


def test_dead_token_user_ids_all_success():
    ids = [uuid4(), uuid4()]
    responses = [FakeSendResponse(True), FakeSendResponse(True)]
    assert dead_token_user_ids(responses, ids) == []


def test_dead_token_user_ids_unregistered():
    ids = [uuid4(), uuid4()]
    responses = [
        FakeSendResponse(True),
        FakeSendResponse(False, messaging.UnregisteredError('gone')),
    ]
    assert dead_token_user_ids(responses, ids) == [ids[1]]


def test_dead_token_user_ids_sender_id_mismatch():
    uid = uuid4()
    responses = [
        FakeSendResponse(False, messaging.SenderIdMismatchError('mismatch')),
    ]
    assert dead_token_user_ids(responses, [uid]) == [uid]


def test_dead_token_user_ids_transient_error_ignored():
    responses = [
        FakeSendResponse(False, messaging.QuotaExceededError('slow down')),
    ]
    assert dead_token_user_ids(responses, [uuid4()]) == []


def test_dead_token_user_ids_empty():
    assert dead_token_user_ids([], []) == []


def test_send_push_no_users(mocker):
    mock_logger = mocker.patch('app.firebase.logger')
    send_push([], 'title', 'body')
    mock_logger.info.assert_any_call('Пустой список получателей. Пуши не отправлены.')


def test_send_push_with_users(mocker):
    mock_send_each = mocker.patch('app.firebase.messaging.send_each')
    mock_send_each.return_value = FakeBatchResponse([FakeSendResponse(True)])
    user = User(id=uuid4(), firebase_push_token='token')

    send_push([user], 'title', 'body', link='http://link')

    mock_send_each.assert_called_once()


def test_send_push_no_token(mocker):
    mock_logger = mocker.patch('app.firebase.logger')
    user = User(id=uuid4(), firebase_push_token=None)

    send_push([user], 'title', 'body')
    mock_logger.warning.assert_called()


def test_send_push_clears_dead_token(mocker, db):
    # Живой юзер в тестовой БД с валидным (пока) токеном
    user = User(
        id=uuid4(),
        display_name='Dead Token',
        firebase_uid=f'uid-{uuid4()}',
        firebase_push_token='stale-token',
        registered_at=datetime(2026, 1, 1),
    )
    db.add(user)
    db.commit()

    mock_send_each = mocker.patch('app.firebase.messaging.send_each')
    mock_send_each.return_value = FakeBatchResponse(
        [FakeSendResponse(False, messaging.UnregisteredError('gone'))]
    )

    send_push([user], 'title', 'body')

    refreshed = db.execute(select(User).where(User.id == user.id)).scalar_one()
    assert refreshed.firebase_push_token is None


def test_create_firebase_user(mocker):
    mock_auth = mocker.patch('app.firebase.auth')
    mock_user = MagicMock()
    mock_user.uid = 'test_uid'
    mock_auth.create_user.return_value = mock_user

    uid = create_firebase_user('name', 'photo', 'email', 'phone')
    assert uid == 'test_uid'
    mock_auth.create_user.assert_called_once_with(
        email='email', email_verified=False, display_name='name', photo_url='photo'
    )


def test_delete_firebase_user(mocker):
    mock_auth = mocker.patch('app.firebase.auth')
    delete_firebase_user('uid')
    mock_auth.delete_user.assert_called_once_with('uid')


def test_create_custom_firebase_token(mocker):
    mock_auth = mocker.patch('app.firebase.auth')
    mock_auth.create_custom_token.return_value = b'token'
    token = create_custom_firebase_token('uid')
    assert token == 'token'


def test_get_firebase_user_data(mocker):
    mock_auth = mocker.patch('app.firebase.auth')
    get_firebase_user_data('uid')
    mock_auth.get_user.assert_called_once_with('uid')
