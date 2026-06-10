from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.cron_scripts.at_noon import (
    get_next_birthday,
    send_upcoming_birthday_of_current_user_notification,
    send_upcoming_birthday_of_followed_user_notification,
)
from app.db import Gender, PushReason, PushSendingLog, User
from app.utils import utc_now


def test_get_next_birthday():
    # Birthday today
    today = datetime.now()
    birth_date = date(1990, today.month, today.day)
    next_bday = get_next_birthday(birth_date)
    assert next_bday.month == birth_date.month
    assert next_bday.day == birth_date.day

    # Birthday tomorrow
    tomorrow = today + timedelta(days=1)
    birth_date = date(1990, tomorrow.month, tomorrow.day)
    next_bday = get_next_birthday(birth_date)
    assert next_bday.month == birth_date.month
    assert next_bday.day == birth_date.day
    assert next_bday.year == today.year

    # Birthday yesterday
    yesterday = today - timedelta(days=1)
    birth_date = date(1990, yesterday.month, yesterday.day)
    next_bday = get_next_birthday(birth_date)
    assert next_bday.year == today.year + 1


@pytest.mark.anyio
async def test_send_upcoming_birthday_of_current_user_notification(db, mocker):
    mock_send_push = mocker.patch('app.cron_scripts.at_noon.send_push')

    bday = (datetime.now() + timedelta(days=15)).date()
    user = User(
        display_name='Birthday User',
        firebase_uid='bday_uid',
        firebase_push_token='token_bday',
        birth_date=bday,
        registered_at=utc_now(),
    )
    db.add(user)
    db.commit()

    send_upcoming_birthday_of_current_user_notification()

    assert mock_send_push.call_count == 1
    log = db.scalars(
        select(PushSendingLog).where(PushSendingLog.target_user_id == user.id)
    ).first()
    assert log is not None
    assert log.reason == PushReason.CURRENT_USER_BIRTHDAY

    mock_send_push.reset_mock()
    send_upcoming_birthday_of_current_user_notification()
    assert mock_send_push.call_count == 0


@pytest.mark.anyio
async def test_send_upcoming_birthday_of_followed_user_notification(db, mocker):
    mock_send_push = mocker.patch('app.cron_scripts.at_noon.send_push')
    mocker.patch(
        'app.cron_scripts.at_noon.get_user_deep_link', return_value='http://link'
    )

    bday = date.today() + timedelta(days=10)
    followed_user = User(
        display_name='Followed',
        firebase_uid='followed_uid',
        birth_date=bday,
        registered_at=utc_now(),
        gender=Gender.female,
    )
    follower = User(
        display_name='Follower',
        firebase_uid='follower_uid',
        firebase_push_token='token_follower',
        registered_at=utc_now(),
    )
    follower.follows.append(followed_user)
    db.add_all([followed_user, follower])
    db.commit()

    send_upcoming_birthday_of_followed_user_notification()

    assert mock_send_push.call_count == 1
    args, kwargs = mock_send_push.call_args
    assert follower in kwargs['target_users']
    assert 'её' in kwargs['body']

    db.refresh(followed_user)
    assert followed_user.pre_bday_push_for_followers_last_sent_at is not None


def test_at_noon_main(mocker):
    from app.cron_scripts.at_noon import main

    mock_1 = mocker.patch(
        'app.cron_scripts.at_noon.send_upcoming_birthday_of_current_user_notification'
    )
    mock_2 = mocker.patch(
        'app.cron_scripts.at_noon.send_upcoming_birthday_of_followed_user_notification'
    )
    main()
    mock_1.assert_called_once()
    mock_2.assert_called_once()


def test_at_noon_script_execution(mocker):
    # This covers line 134: if __name__ == '__main__': main()
    import os
    import runpy

    from app.cron_scripts import at_noon

    # Mock dependencies to avoid DB side effects and network calls
    mocker.patch('app.db.SessionLocal')
    mocker.patch('app.firebase.send_push')

    script_path = os.path.abspath(at_noon.__file__)
    runpy.run_path(script_path, run_name='__main__')


def test_send_upcoming_birthday_current_user_no_token(db, mocker):
    mock_send_push = mocker.patch('app.cron_scripts.at_noon.send_push')
    bday = (datetime.now() + timedelta(days=5)).date()
    user = User(
        display_name='No Token User',
        firebase_uid='no_token_uid',
        firebase_push_token=None,
        birth_date=bday,
        registered_at=utc_now(),
    )
    db.add(user)
    db.commit()
    send_upcoming_birthday_of_current_user_notification()
    mock_send_push.assert_not_called()


def test_send_upcoming_birthday_followed_no_token(db, mocker):
    mock_send_push = mocker.patch('app.cron_scripts.at_noon.send_push')
    bday = date.today() + timedelta(days=10)
    followed = User(
        display_name='F', firebase_uid='f_uid', birth_date=bday, registered_at=utc_now()
    )
    follower = User(
        display_name='R',
        firebase_uid='r_uid',
        firebase_push_token=None,
        registered_at=utc_now(),
    )
    follower.follows.append(followed)
    db.add_all([followed, follower])
    db.commit()
    send_upcoming_birthday_of_followed_user_notification()
    mock_send_push.assert_not_called()
