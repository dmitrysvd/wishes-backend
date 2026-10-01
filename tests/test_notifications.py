from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.constants import (
    RESERVATION_PUSH_MAX_AGE,
    WISH_CREATION_PUSH_DELAY,
    WISH_CREATION_PUSH_HOURS_UTC,
    WISH_CREATION_PUSH_MIN_INTERVAL,
    FollowAction,
)
from app.db import (
    FollowEvent,
    Gender,
    PushInstallation,
    PushReason,
    PushSendingLog,
    User,
    Wish,
)
from app.helpers.user_helpers import get_followers_push_link, get_push_deep_link
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
        push_installations=[PushInstallation(push_token='token1')],
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
        push_installations=[],
        registered_at=utc_now(),
        gender=Gender.female,
    )
    db.add(user)
    db.commit()
    return user


@pytest.mark.anyio
async def test_send_reservation_notifications(
    db, user_with_token, user_without_token, fcm
):

    # Владельцу без установок пуш не шлём.
    db.add_all(
        [
            Wish(
                name='Wish 1',
                user_id=user_with_token.id,
                reserved_by_id=user_without_token.id,
                reserved_at=utc_now(),
            ),
            Wish(
                name='Wish 2',
                user_id=user_without_token.id,
                reserved_by_id=user_with_token.id,
                reserved_at=utc_now(),
            ),
        ]
    )
    db.commit()

    send_reservation_notifincations()

    assert len(fcm.calls) == 1
    assert fcm.tokens == ['token1']
    assert fcm.messages[0].android.notification.title
    assert fcm.messages[0].android.notification.body


def _reserve(db, wish: Wish, by: User) -> None:
    wish.reserved_by_id = by.id
    wish.reserved_at = utc_now()
    db.commit()


def test_reservation_push_once_per_reservation(
    db, user_with_token, user_without_token, fcm
):
    wish = Wish(name='Wish', user_id=user_with_token.id)
    db.add(wish)
    db.commit()
    _reserve(db, wish, user_without_token)

    send_reservation_notifincations()
    send_reservation_notifincations()

    assert len(fcm.messages) == 1


def test_reservation_push_for_each_new_reservation(
    db, user_with_token, user_without_token, fcm
):
    """Вторая бронь после пуша — снова пуш: другой хотелки и той же после снятия."""
    first, second = (
        Wish(name='A', user_id=user_with_token.id),
        Wish(name='B', user_id=user_with_token.id),
    )
    db.add_all([first, second])
    db.commit()

    _reserve(db, first, user_without_token)
    send_reservation_notifincations()
    _reserve(db, second, user_without_token)
    send_reservation_notifincations()
    first.reserved_by_id = None
    first.reserved_at = None
    db.commit()
    _reserve(db, first, user_without_token)
    send_reservation_notifincations()

    assert len(fcm.messages) == 3


def test_no_reservation_push_for_old_reservation(
    db, user_with_token, user_without_token, fcm
):
    now = utc_now()
    db.add(
        Wish(
            name='Wish',
            user_id=user_with_token.id,
            reserved_by_id=user_without_token.id,
            reserved_at=now - RESERVATION_PUSH_MAX_AGE - timedelta(minutes=1),
        )
    )
    db.commit()

    send_reservation_notifincations(now)

    assert fcm.messages == []


# Сегодня 12:00 UTC — внутри окна отправки. Привязано к реальным суткам, потому
# что `sent_at` в логе пишется реальными часами, а рейт-лимит сравнивает с ним.
IN_WINDOW = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)


@pytest.fixture
def followed_author(db, user_with_token, user_without_token) -> User:
    """`user_without_token` подписан на автора `user_with_token`; у автора одна
    созревшая хотелка (старше DELAY) и одна свежая."""
    user_without_token.follows.append(user_with_token)
    db.add_all(
        [
            Wish(
                name='Old Wish',
                user_id=user_with_token.id,
                created_at=IN_WINDOW - WISH_CREATION_PUSH_DELAY - timedelta(minutes=1),
                is_creation_notification_sent=False,
            ),
            Wish(
                name='New Wish',
                user_id=user_with_token.id,
                created_at=IN_WINDOW - timedelta(minutes=5),
                is_creation_notification_sent=False,
            ),
        ]
    )
    db.commit()
    return user_with_token


def _wish_flags(db, author: User) -> dict[str, bool]:
    return {
        w.name: w.is_creation_notification_sent
        for w in db.scalars(select(Wish).where(Wish.user_id == author.id))
    }


@pytest.mark.anyio
async def test_send_wish_creation_notifications(
    db, followed_author, user_without_token, fcm
):
    # Подписчик без установок: пуша нет, но созревшая хотелка помечена.
    send_wish_creation_notifications(now=IN_WINDOW)
    assert fcm.calls == []
    assert _wish_flags(db, followed_author) == {'Old Wish': True, 'New Wish': False}

    user_without_token.push_installations = [PushInstallation(push_token='token2')]
    db.add(user_without_token)
    db.commit()

    # Свежая хотелка созрела: уходит один пуш подписчику.
    send_wish_creation_notifications(now=IN_WINDOW + WISH_CREATION_PUSH_DELAY)
    assert len(fcm.calls) == 1
    (message,) = fcm.messages
    assert message.token == 'token2'
    notification = message.android.notification
    assert 'обновил' in notification.title  # автор — мужчина
    assert notification.body == 'Узнайте, что User with Token хочет получить в подарок'
    assert _wish_flags(db, followed_author) == {'Old Wish': True, 'New Wish': True}


@pytest.mark.anyio
async def test_send_wish_creation_notifications_outside_window(
    db, followed_author, fcm
):
    # Вне окна прогон не шлёт и не помечает — хотелки дождутся окна.
    night = IN_WINDOW.replace(hour=WISH_CREATION_PUSH_HOURS_UTC.stop)
    send_wish_creation_notifications(now=night)
    assert fcm.calls == []
    assert _wish_flags(db, followed_author) == {'Old Wish': False, 'New Wish': False}


@pytest.mark.anyio
async def test_send_wish_creation_notifications_rate_limit(
    db, followed_author, user_without_token, fcm
):
    user_without_token.push_installations = [PushInstallation(push_token='token2')]
    db.add(user_without_token)
    db.commit()

    send_wish_creation_notifications(now=IN_WINDOW)
    assert len(fcm.calls) == 1

    # Созрела вторая хотелка, но с прошлого пуша этой паре прошло меньше
    # интервала: пуша нет, хотелка помечена и в следующий пуш не попадёт.
    send_wish_creation_notifications(now=IN_WINDOW + WISH_CREATION_PUSH_DELAY)
    assert len(fcm.calls) == 1
    assert _wish_flags(db, followed_author) == {'Old Wish': True, 'New Wish': True}

    # Интервал прошёл (с запасом от реального `sent_at` в логе), есть новая
    # созревшая хотелка — пуш снова уходит.
    db.add(
        Wish(
            name='Later Wish',
            user_id=followed_author.id,
            created_at=IN_WINDOW,
            is_creation_notification_sent=False,
        )
    )
    db.commit()
    send_wish_creation_notifications(
        now=IN_WINDOW + WISH_CREATION_PUSH_MIN_INTERVAL + timedelta(days=2)
    )
    assert len(fcm.calls) == 2


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
        push_installations=[PushInstallation(push_token=token)] if token else [],
        registered_at=utc_now(),
    )
    db.add(user)
    db.commit()
    return user


def test_new_follower_single(db, fcm):
    target = _user(db, 'Target', 'token-target')
    follower = _user(db, 'Follower', None)
    event = _follow(db, follower, target)

    send_new_follower_notifications()

    (message,) = fcm.messages
    assert message.token == 'token-target'
    assert message.android.notification.body == 'На вас подписался Follower'
    assert message.data['link'] == get_push_deep_link(follower)
    assert message.data['type'] == 'new_follower'
    db.refresh(event)
    assert event.is_notification_sent is True
    log = db.scalars(
        select(PushSendingLog).where(PushSendingLog.reason == PushReason.NEW_FOLLOWER)
    ).one()
    assert log.reason_user_id == follower.id
    assert message.data['delivery_id'] == str(log.id)

    # Повторный прогон — событие уже отмечено, пуша нет.
    fcm.clear()
    send_new_follower_notifications()
    assert fcm.calls == []


def test_new_follower_many_in_one_push(db, fcm):
    target = _user(db, 'Target', 'token-target')
    first = _user(db, 'First', None)
    second = _user(db, 'Second', None)
    _follow(db, first, target)
    _follow(db, second, target)

    send_new_follower_notifications()

    (message,) = fcm.messages
    assert message.android.notification.body == 'На вас подписались First и ещё 1'
    assert message.data['link'] == get_followers_push_link(target)


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
