from uuid import uuid4

from firebase_admin import exceptions, messaging
from sqlalchemy import select

from app.db import PushInstallation, User
from app.firebase import AddressCheck, check_address
from app.push_installations import fid_candidate, migrate_legacy_tokens
from app.utils import utc_now


def _legacy_user(db, token: str | None) -> User:
    user = User(
        display_name='Legacy',
        firebase_uid=f'uid-{uuid4()}',
        firebase_push_token=token,
        firebase_push_token_saved_at=utc_now() if token else None,
        registered_at=utc_now(),
    )
    db.add(user)
    db.commit()
    return user


def test_fid_candidate():
    assert fid_candidate('dQw4w9WgXcQ:APA91b') == 'dQw4w9WgXcQ'
    assert fid_candidate('no-colon') is None
    assert fid_candidate(':APA91b') is None


def _checker(fid_results: dict[str, AddressCheck], token_results: dict):
    def check(*, fid=None, token=None):
        return fid_results[fid] if fid is not None else token_results[token]

    return check


def test_migrate_legacy_tokens_outcomes(db):
    with_fid = _legacy_user(db, 'F1:tok1')
    token_only = _legacy_user(db, 'F2:tok2')  # FID не подтвердился, токен жив
    dead = _legacy_user(db, 'F3:tok3')
    unknown_fid = _legacy_user(db, 'F4:tok4')
    unknown_token = _legacy_user(db, 'nocolon5')
    _legacy_user(db, None)  # без адреса — вне выборки
    already = _legacy_user(db, 'F6:tok6')  # уже есть установка — не трогаем
    db.add(PushInstallation(user_id=already.id, push_token='other'))
    db.commit()

    check = _checker(
        {
            'F1': AddressCheck.ok,
            'F2': AddressCheck.dead,
            'F3': AddressCheck.dead,
            'F4': AddressCheck.unknown,
        },
        {
            'F2:tok2': AddressCheck.ok,
            'F3:tok3': AddressCheck.dead,
            'nocolon5': AddressCheck.unknown,
        },
    )

    report = migrate_legacy_tokens(db, check=check)

    assert report.with_fid == [with_fid.id]
    assert report.token_only == [token_only.id]
    assert report.dead == [dead.id]
    assert sorted(report.unknown) == sorted([unknown_fid.id, unknown_token.id])
    rows = {
        row.user_id: (row.fid, row.push_token)
        for row in db.scalars(select(PushInstallation))
    }
    assert rows[with_fid.id] == ('F1', 'F1:tok1')
    assert rows[token_only.id] == (None, 'F2:tok2')
    assert rows[already.id] == (None, 'other')
    assert dead.id not in rows and unknown_fid.id not in rows
    # Старую колонку не трогаем — снимок для отката.
    db.refresh(with_fid)
    assert with_fid.firebase_push_token == 'F1:tok1'

    # Идемпотентность: повторный прогон видит только «неизвестных».
    report2 = migrate_legacy_tokens(db, check=check)
    assert report2.with_fid == [] and report2.token_only == []
    assert sorted(report2.unknown) == sorted(report.unknown)


def test_migrate_legacy_tokens_dry_run_writes_nothing(db):
    user = _legacy_user(db, 'F1:tok1')
    check = _checker({'F1': AddressCheck.ok}, {})

    report = migrate_legacy_tokens(db, check=check, dry_run=True)

    assert report.with_fid == [user.id]
    assert db.scalars(select(PushInstallation)).all() == []


def test_check_address(mocker):
    send = mocker.patch('app.firebase.messaging.send')

    assert check_address(fid='F') == AddressCheck.ok
    (message,) = send.call_args.args
    assert (message.fid, message.token) == ('F', None)
    assert send.call_args.kwargs == {'dry_run': True}

    send.side_effect = messaging.UnregisteredError('gone')
    assert check_address(token='T') == AddressCheck.dead
    send.side_effect = exceptions.UnavailableError('down')
    assert check_address(token='T') == AddressCheck.unknown
