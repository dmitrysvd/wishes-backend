from datetime import timedelta

import pytest

from app.db import Gender, User, Wish
from app.notifications import (
    send_reservation_notifincations,
    send_wish_creation_notifications,
)
from app.utils import utc_now


@pytest.fixture
def user_with_token(db):
    user = User(
        display_name='User with Token',
        firebase_uid='uid1',
        firebase_push_token='token1',
        registered_at=utc_now(),
        gender=Gender.male,
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def user_without_token(db):
    user = User(
        display_name='User without Token',
        firebase_uid='uid2',
        firebase_push_token=None,
        registered_at=utc_now(),
        gender=Gender.female,
    )
    db.add(user)
    db.commit()
    return user


@pytest.mark.anyio
async def test_send_reservation_notifications(
    db, user_with_token, user_without_token, mocker
):
    mock_send_push = mocker.patch('app.notifications.send_push')

    # Wish for user_with_token, reserved by someone
    wish1 = Wish(
        name='Wish 1',
        user_id=user_with_token.id,
        reserved_by_id=user_without_token.id,
        is_reservation_notification_sent=False,
    )
    # Wish for user_without_token, reserved by someone
    wish2 = Wish(
        name='Wish 2',
        user_id=user_without_token.id,
        reserved_by_id=user_with_token.id,
        is_reservation_notification_sent=False,
    )

    db.add_all([wish1, wish2])
    db.commit()

    send_reservation_notifincations()

    mock_send_push.assert_called_once()
    args, kwargs = mock_send_push.call_args
    assert user_with_token in kwargs['target_users']
    assert user_without_token not in kwargs['target_users']
    assert kwargs['title']
    assert kwargs['body']

    # Flags should be updated for both if they were matched by the query
    # Actually, the code updates only for users_to_send_pushes (those with tokens)
    db.refresh(wish1)
    db.refresh(wish2)
    assert wish1.is_reservation_notification_sent is True
    assert (
        wish2.is_reservation_notification_sent is False
    )  # No token, no notification sent/marked


@pytest.mark.anyio
async def test_send_wish_creation_notifications(
    db, user_with_token, user_without_token, mocker
):
    mock_send_push = mocker.patch('app.notifications.send_push')
    mocker.patch('app.notifications.get_user_deep_link', return_value='http://link')

    # user_without_token follows user_with_token
    user_without_token.follows.append(user_with_token)
    # user_with_token follows user_without_token
    user_with_token.follows.append(user_without_token)

    # Old wish (created > 30 mins ago)
    old_time = utc_now() - timedelta(minutes=40)
    wish1 = Wish(
        name='Old Wish',
        user_id=user_with_token.id,
        created_at=old_time,
        is_creation_notification_sent=False,
    )

    # New wish (created just now)
    wish2 = Wish(
        name='New Wish',
        user_id=user_with_token.id,
        created_at=utc_now(),
        is_creation_notification_sent=False,
    )

    db.add_all([wish1, wish2])
    db.commit()

    send_wish_creation_notifications()

    mock_send_push.assert_not_called()

    # Mark follower with token
    user_without_token.firebase_push_token = 'token2'
    db.add(user_without_token)
    # Reset flag for wish1
    wish1.is_creation_notification_sent = False
    db.add(wish1)
    db.commit()

    send_wish_creation_notifications()

    mock_send_push.assert_called_once()
    args, kwargs = mock_send_push.call_args
    assert user_without_token in kwargs['target_users']
    assert 'обновил' in kwargs['title']  # user_with_token is male
    assert kwargs['body'] == 'Узнайте, что User with Token хочет получить в подарок'

    db.refresh(wish1)
    assert wish1.is_creation_notification_sent is True
