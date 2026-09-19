from datetime import datetime
from unittest.mock import MagicMock
from uuid import uuid4

from firebase_admin import messaging
from sqlalchemy import select

from app.db import PushInstallation, PushReason, PushSendingLog, User
from app.firebase import (
    create_custom_firebase_token,
    create_firebase_user,
    dead_installation_ids,
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


def _installations(n: int) -> list[PushInstallation]:
    return [PushInstallation(id=uuid4(), push_token=f't{i}') for i in range(n)]


def test_dead_installation_ids_all_success():
    installations = _installations(2)
    responses = [FakeSendResponse(True), FakeSendResponse(True)]
    assert dead_installation_ids(responses, installations) == []


def test_dead_installation_ids_unregistered():
    installations = _installations(2)
    responses = [
        FakeSendResponse(True),
        FakeSendResponse(False, messaging.UnregisteredError('gone')),
    ]
    assert dead_installation_ids(responses, installations) == [installations[1].id]


def test_dead_installation_ids_sender_id_mismatch():
    (installation,) = _installations(1)
    responses = [FakeSendResponse(False, messaging.SenderIdMismatchError('bad'))]
    assert dead_installation_ids(responses, [installation]) == [installation.id]


def test_dead_installation_ids_transient_error_ignored():
    responses = [FakeSendResponse(False, messaging.QuotaExceededError('quota'))]
    assert dead_installation_ids(responses, _installations(1)) == []


def test_dead_installation_ids_empty():
    assert dead_installation_ids([], []) == []


def test_send_push_no_users(mocker):
    mock_logger = mocker.patch('app.firebase.logger')
    send_push([], 'title', 'body', reason=PushReason.SEASONAL)
    mock_logger.info.assert_any_call('Пустой список получателей. Пуши не отправлены.')


def _persisted_user(db, *addresses: str) -> User:
    """Юзер в тестовой БД с установками: `'tok'` — только токен, `'fid:…'` —
    установка с FID (токен — 'tok-for-<fid>')."""
    user = User(
        id=uuid4(),
        display_name='Push Target',
        firebase_uid=f'uid-{uuid4()}',
        push_installations=[
            PushInstallation(fid=a[4:], push_token=f'tok-for-{a[4:]}')
            if a.startswith('fid:')
            else PushInstallation(push_token=a)
            for a in addresses
        ],
        registered_at=datetime(2026, 1, 1),
    )
    db.add(user)
    db.commit()
    return user


def test_send_push_with_users(fcm, db):
    user = _persisted_user(db, 'token')
    culprit = _persisted_user(db)

    send_push(
        [user],
        'title',
        'body',
        reason=PushReason.SEASONAL,
        reason_user=culprit,
        campaign_key='ny-2026',
        link='http://link',
    )

    (message,) = fcm.messages
    assert message.token == 'token'
    assert message.data['link'] == 'http://link'
    # Лог пишет сама send_push — единственная точка отправки.
    log = db.scalars(select(PushSendingLog)).one()
    assert log.target_user_id == user.id
    assert log.reason == PushReason.SEASONAL
    assert log.reason_user_id == culprit.id
    assert log.campaign_key == 'ny-2026'


def test_send_push_logs_self_as_reason_user_by_default(fcm, db):
    user = _persisted_user(db, 'token')

    send_push([user], 'title', 'body', reason=PushReason.RESERVATION)

    log = db.scalars(select(PushSendingLog)).one()
    assert log.reason_user_id == user.id
    assert log.campaign_key is None


def test_send_push_no_installations(mocker, db):
    mock_logger = mocker.patch('app.firebase.logger')
    user = _persisted_user(db)

    outcome = send_push([user], 'title', 'body', reason=PushReason.SEASONAL)
    mock_logger.warning.assert_called()
    assert outcome.sent_user_ids == frozenset()


def test_send_push_fid_first_then_token_and_one_log_row_per_user(fcm, db):
    # Две установки: с FID (шлём по fid) и без (по токену); лог — одна строка,
    # delivery_id у обоих сообщений один и тот же.
    user = _persisted_user(db, 'fid:F1', 'plain-token')

    outcome = send_push(
        [user], 'title', 'body', reason=PushReason.PRICE_ALERT, with_delivery_id=True
    )

    by_fid, by_token = sorted(fcm.messages, key=lambda m: m.token or '')
    assert (by_fid.fid, by_fid.token) == ('F1', None)
    assert (by_token.fid, by_token.token) == (None, 'plain-token')
    assert by_fid.data['delivery_id'] == by_token.data['delivery_id']
    assert outcome.sent_user_ids == frozenset({user.id})
    assert outcome.accepted_user_ids == frozenset({user.id})
    (log,) = db.scalars(select(PushSendingLog)).all()
    assert str(log.id) == by_fid.data['delivery_id']


def test_send_push_deletes_dead_installation_keeps_live_one(mocker, db):
    user = _persisted_user(db, 'fid:DEAD', 'live-token')

    mock_send_each = mocker.patch('app.firebase.messaging.send_each')

    def respond(messages, dry_run=False):
        return FakeBatchResponse(
            [
                FakeSendResponse(False, messaging.UnregisteredError('gone'))
                if m.fid == 'DEAD'
                else FakeSendResponse(True)
                for m in messages
            ]
        )

    mock_send_each.side_effect = respond

    outcome = send_push([user], 'title', 'body', reason=PushReason.SEASONAL)

    db.expire_all()
    assert [i.push_token for i in user.push_installations] == ['live-token']
    # Юзер принят: хотя бы одна установка дошла.
    assert outcome.accepted_user_ids == frozenset({user.id})


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
