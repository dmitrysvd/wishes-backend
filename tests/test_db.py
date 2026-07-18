from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError

from app.db import User, Wish
from app.utils import utc_now


def test_user_repr_str():
    user = User(display_name='Test')
    assert 'Test' in repr(user)
    assert 'Test' in str(user)


def test_push_token_empty_string_rejected(db):
    # CHECK push_token_not_empty: '' в firebase_push_token недопустима (NULL — ок).
    user = User(
        display_name='Empty Token',
        firebase_uid='empty_token_uid',
        firebase_push_token='',
        registered_at=utc_now(),
    )
    db.add(user)
    with pytest.raises(IntegrityError):
        db.commit()


def test_wish_str():
    wish = Wish(name='Gift')
    assert 'Gift' in str(wish)


def test_wish_is_reserved():
    wish = Wish(reserved_by_id=None)
    assert not wish.is_reserved
    wish.reserved_by_id = UUID('50e7825d-3a7f-7ef3-9c0d-330a69e9ac35')
    assert wish.is_reserved


def test_wish_active_query():
    query = Wish.get_active_wish_query()
    assert 'is_archived' in str(query)


def test_db_events(mocker):
    from sqlite3 import Connection

    from app.db import do_begin, do_connect

    # Test do_connect with non-sqlite (coverage for line 215-216)
    mock_conn_other = mocker.Mock()
    do_connect(mock_conn_other, None)

    # Test do_connect with sqlite
    mock_sqlite = mocker.Mock(spec=Connection)
    mock_cursor = mock_sqlite.cursor.return_value
    do_connect(mock_sqlite, None)
    assert mock_sqlite.isolation_level is None
    mock_cursor.execute.assert_called_with('PRAGMA foreign_keys=ON;')

    # Test do_begin
    mock_engine_conn = mocker.Mock()
    do_begin(mock_engine_conn)
    mock_engine_conn.exec_driver_sql.assert_called_with('BEGIN')
