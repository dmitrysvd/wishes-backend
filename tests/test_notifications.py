from datetime import timedelta

import pytest
from sqlalchemy import select

from app.constants import FollowAction
from app.db import FollowEvent, Gender, PushReason, PushSendingLog, User, Wish
from app.notifications import (
    send_new_follower_notifications,
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
    db, user_with_token, user_without_token, mocker, fcm
):

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

    assert len(fcm.calls) == 1
    assert fcm.tokens == [user_with_token.firebase_push_token]
    assert fcm.messages[0].android.notification.title
    assert fcm.messages[0].android.notification.body

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
    db, user_with_token, user_without_token, mocker, fcm
):
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

    assert fcm.calls == []

    # Mark follower with token
    user_without_token.firebase_push_token = 'token2'
    db.add(user_without_token)
    # Reset flag for wish1
    wish1.is_creation_notification_sent = False
    db.add(wish1)
    db.commit()

    send_wish_creation_notifications()

    assert len(fcm.calls) == 1
    (message,) = fcm.messages
    assert message.token == user_without_token.firebase_push_token
    notification = message.android.notification
    assert 'обновил' in notification.title  # user_with_token is male
    assert notification.body == 'Узнайте, что User with Token хочет получить в подарок'

    db.refresh(wish1)
    assert wish1.is_creation_notification_sent is True


def _follow(db, actor: User, target: User) -> FollowEvent:
    actor.follows.append(target)
    event = FollowEvent(
        actor_id=actor.id, target_id=target.id, action=FollowAction.follow
    )
    db.add(event)
    db.commit()
    return event


def _user(db, name: str, token: str | None) -> User:
    user = User(
        display_name=name,
        firebase_uid=f'uid-{name}',
        firebase_push_token=token,
        registered_at=utc_now(),
    )
    db.add(user)
    db.commit()
    return user


def test_new_follower_single(db, fcm, mocker):
    mocker.patch(
        'app.notifications.get_user_deep_link', side_effect=lambda u: f'link:{u.id}'
    )
    target = _user(db, 'Target', 'token-target')
    follower = _user(db, 'Follower', None)
    event = _follow(db, follower, target)

    send_new_follower_notifications()

    (message,) = fcm.messages
    assert message.token == 'token-target'
    assert message.android.notification.body == 'На вас подписался Follower'
    assert message.data['link'] == f'link:{follower.id}'
    db.refresh(event)
    assert event.is_notification_sent is True
    log = db.scalars(
        select(PushSendingLog).where(PushSendingLog.reason == PushReason.NEW_FOLLOWER)
    ).one()
    assert log.reason_user_id == follower.id

    # Повторный прогон — событие уже отмечено, пуша нет.
    fcm.clear()
    send_new_follower_notifications()
    assert fcm.calls == []


def test_new_follower_many_in_one_push(db, fcm, mocker):
    mocker.patch(
        'app.notifications.get_user_deep_link', side_effect=lambda u: f'link:{u.id}'
    )
    target = _user(db, 'Target', 'token-target')
    first = _user(db, 'First', None)
    second = _user(db, 'Second', None)
    _follow(db, first, target)
    _follow(db, second, target)

    send_new_follower_notifications()

    (message,) = fcm.messages
    assert message.android.notification.body == 'На вас подписались First и ещё 1'
    assert message.data['link'] == f'link:{target.id}'


def test_new_follower_skips_unfollowed_and_no_token(db, fcm):
    target = _user(db, 'Target', 'token-target')
    no_token_target = _user(db, 'Silent', None)
    fickle = _user(db, 'Fickle', None)
    event = _follow(db, fickle, target)
    fickle.follows.remove(target)
    _follow(db, fickle, no_token_target)
    db.commit()

    send_new_follower_notifications()

    assert fcm.calls == []
    db.refresh(event)
    assert event.is_notification_sent is True
