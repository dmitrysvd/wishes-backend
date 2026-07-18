from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.cron_scripts.at_noon import (
    SeasonalCampaign,
    followers_push_recently_sent,
    get_next_birthday,
    is_in_campaign_window,
    send_seasonal_notifications,
    send_upcoming_birthday_of_current_user_notification,
    send_upcoming_birthday_of_followed_user_notification,
)
from app.db import Gender, PushReason, PushSendingLog, User
from app.utils import utc_now


def _today_campaign(window_days: int = 0) -> SeasonalCampaign:
    # Кампания с якорем на сегодня — окно всегда включает текущий день.
    today = date.today()
    return SeasonalCampaign(
        key='test',
        month=today.month,
        day=today.day,
        window_days=window_days,
        title='Тестовый повод',
        body='Тестовое тело',
    )


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


def test_get_next_birthday_feb29_does_not_crash():
    # 29 февраля — не должно падать в невисокосный год.
    next_bday = get_next_birthday(date(2000, 2, 29))
    assert next_bday.month == 2
    assert next_bday.day in (28, 29)
    assert next_bday >= datetime.now()


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

    # Факт отправки записан в лог (наблюдаемость follower-ДР-пуша).
    log = db.scalars(
        select(PushSendingLog).where(
            PushSendingLog.reason == PushReason.FOLLOWER_BIRTHDAY
        )
    ).first()
    assert log is not None
    assert log.reason_user_id == followed_user.id
    assert log.target_user_id == follower.id

    db.refresh(followed_user)
    assert followed_user.pre_bday_push_for_followers_last_sent_at is not None


def test_followers_push_recently_sent():
    assert followers_push_recently_sent(None) is False
    # aware-время приводится к naive перед сравнением
    assert followers_push_recently_sent(datetime.now(timezone.utc)) is True
    assert followers_push_recently_sent(datetime(2000, 1, 1)) is False


@pytest.mark.anyio
async def test_followed_user_push_skipped_when_recently_sent(db, mocker):
    # Уведомление подписчикам не шлётся повторно, если уже отправляли недавно
    # (last_sent — aware-время, проверяется ветка приведения к naive).
    mock_send_push = mocker.patch('app.cron_scripts.at_noon.send_push')
    bday = date.today() + timedelta(days=10)
    followed = User(
        display_name='Recently Notified',
        firebase_uid='recent_uid',
        birth_date=bday,
        registered_at=utc_now(),
        pre_bday_push_for_followers_last_sent_at=utc_now(),
    )
    follower = User(
        display_name='Follower',
        firebase_uid='recent_follower_uid',
        firebase_push_token='token_recent',
        registered_at=utc_now(),
    )
    follower.follows.append(followed)
    db.add_all([followed, follower])
    db.commit()

    send_upcoming_birthday_of_followed_user_notification()

    mock_send_push.assert_not_called()


def test_is_in_campaign_window():
    campaign = SeasonalCampaign(
        key='mar8', month=3, day=8, window_days=7, title='t', body='b'
    )
    # Ровно на якоре — в окне.
    assert is_in_campaign_window(campaign, date(2026, 3, 8)) is True
    # За день до конца окна (якорь - 7) — в окне.
    assert is_in_campaign_window(campaign, date(2026, 3, 1)) is True
    # За день до начала окна — вне.
    assert is_in_campaign_window(campaign, date(2026, 2, 28)) is False
    # После якоря — вне.
    assert is_in_campaign_window(campaign, date(2026, 3, 9)) is False


@pytest.mark.anyio
async def test_seasonal_sent_in_window(db, mocker):
    mock_send_push = mocker.patch('app.cron_scripts.at_noon.send_push')
    mocker.patch(
        'app.cron_scripts.at_noon.get_user_deep_link', return_value='http://own'
    )
    mocker.patch('app.cron_scripts.at_noon.SEASONAL_CAMPAIGNS', (_today_campaign(),))
    user = User(
        display_name='Seasonal User',
        firebase_uid='seasonal_uid',
        firebase_push_token='token_seasonal',
        registered_at=utc_now(),
    )
    db.add(user)
    db.commit()

    send_seasonal_notifications()

    assert mock_send_push.call_count == 1
    _, kwargs = mock_send_push.call_args
    assert kwargs['link'] == 'http://own'
    log = db.scalars(
        select(PushSendingLog).where(PushSendingLog.reason == PushReason.SEASONAL)
    ).first()
    assert log is not None
    # campaign_key содержит год якоря (текущий).
    assert log.campaign_key == f'test-{date.today().year}'
    assert log.target_user_id == user.id


@pytest.mark.anyio
async def test_seasonal_dedup_same_season(db, mocker):
    mock_send_push = mocker.patch('app.cron_scripts.at_noon.send_push')
    mocker.patch('app.cron_scripts.at_noon.get_user_deep_link', return_value='x')
    mocker.patch('app.cron_scripts.at_noon.SEASONAL_CAMPAIGNS', (_today_campaign(),))
    user = User(
        display_name='Dedup User',
        firebase_uid='dedup_uid',
        firebase_push_token='token_dedup',
        registered_at=utc_now(),
    )
    db.add(user)
    db.commit()

    send_seasonal_notifications()
    assert mock_send_push.call_count == 1

    # Повтор в тот же сезон — не шлём.
    mock_send_push.reset_mock()
    send_seasonal_notifications()
    mock_send_push.assert_not_called()


@pytest.mark.anyio
async def test_seasonal_skips_user_without_token(db, mocker):
    mock_send_push = mocker.patch('app.cron_scripts.at_noon.send_push')
    mocker.patch('app.cron_scripts.at_noon.get_user_deep_link', return_value='x')
    mocker.patch('app.cron_scripts.at_noon.SEASONAL_CAMPAIGNS', (_today_campaign(),))
    # Один юзер с пустой строкой-токеном (проходит isnot(None), но falsy),
    # второй с None — оба должны быть пропущены.
    empty = User(
        display_name='Empty Token',
        firebase_uid='empty_token_uid',
        firebase_push_token='',
        registered_at=utc_now(),
    )
    none_token = User(
        display_name='No Token',
        firebase_uid='none_token_uid',
        firebase_push_token=None,
        registered_at=utc_now(),
    )
    db.add_all([empty, none_token])
    db.commit()

    send_seasonal_notifications()
    mock_send_push.assert_not_called()


@pytest.mark.anyio
async def test_seasonal_not_sent_out_of_window(db, mocker):
    mock_send_push = mocker.patch('app.cron_scripts.at_noon.send_push')
    mocker.patch('app.cron_scripts.at_noon.is_in_campaign_window', return_value=False)
    mocker.patch('app.cron_scripts.at_noon.SEASONAL_CAMPAIGNS', (_today_campaign(),))
    user = User(
        display_name='Out Of Window',
        firebase_uid='oow_uid',
        firebase_push_token='token_oow',
        registered_at=utc_now(),
    )
    db.add(user)
    db.commit()

    send_seasonal_notifications()
    mock_send_push.assert_not_called()


def test_at_noon_main(mocker):
    from app.cron_scripts.at_noon import main

    mock_1 = mocker.patch(
        'app.cron_scripts.at_noon.send_upcoming_birthday_of_current_user_notification'
    )
    mock_2 = mocker.patch(
        'app.cron_scripts.at_noon.send_upcoming_birthday_of_followed_user_notification'
    )
    mock_3 = mocker.patch('app.cron_scripts.at_noon.send_seasonal_notifications')
    main()
    mock_1.assert_called_once()
    mock_2.assert_called_once()
    mock_3.assert_called_once()


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
    # Никому не отправили -> timestamp не сжигаем (иначе 200-дневный гвард
    # заблокировал бы будущих подписчиков, оформившихся ещё в окне).
    db.refresh(followed)
    assert followed.pre_bday_push_for_followers_last_sent_at is None


@pytest.mark.anyio
async def test_current_user_no_push_when_birthday_far(db, mocker):
    # ДР дальше окна в 21 день -> уведомление не отправляется.
    mock_send_push = mocker.patch('app.cron_scripts.at_noon.send_push')
    bday = (datetime.now() + timedelta(days=60)).date()
    user = User(
        display_name='Far Birthday',
        firebase_uid='far_uid',
        firebase_push_token='token_far',
        birth_date=bday,
        registered_at=utc_now(),
    )
    db.add(user)
    db.commit()

    send_upcoming_birthday_of_current_user_notification()
    mock_send_push.assert_not_called()


@pytest.mark.anyio
async def test_followed_user_no_push_when_birthday_outside_window(db, mocker):
    # ДР подписки вне окна 3..14 дней -> подписчикам не отправляется.
    mock_send_push = mocker.patch('app.cron_scripts.at_noon.send_push')
    mocker.patch(
        'app.cron_scripts.at_noon.get_user_deep_link', return_value='http://link'
    )
    bday = date.today() + timedelta(days=30)
    followed = User(
        display_name='Far Followed',
        firebase_uid='far_followed_uid',
        birth_date=bday,
        registered_at=utc_now(),
    )
    follower = User(
        display_name='Follower',
        firebase_uid='far_follower_uid',
        firebase_push_token='token_follower2',
        registered_at=utc_now(),
    )
    follower.follows.append(followed)
    db.add_all([followed, follower])
    db.commit()

    send_upcoming_birthday_of_followed_user_notification()
    mock_send_push.assert_not_called()
    db.refresh(followed)
    assert followed.pre_bday_push_for_followers_last_sent_at is None
