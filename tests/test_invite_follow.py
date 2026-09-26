"""Взаимные подписки по инвайт-ссылке и пуш пригласившему (фича 0024)."""

from uuid import uuid4

from sqlalchemy import select

from app.config import settings
from app.constants import (
    FollowAction,
    FollowEventSource,
    FollowSource,
    Gender,
    NotificationGroup,
)
from app.db import (
    FollowEvent,
    NotificationSetting,
    PushInstallation,
    PushReason,
    PushSendingLog,
    User,
)
from app.notifications import send_new_follower_notifications
from app.schemas import RegistrationAttributionSchema
from app.utils import (
    create_invite_mutual_follow,
    follow_each_other_by_invite,
    utc_now,
)


def _user(
    db, name: str, *, token: str | None = None, gender: Gender | None = None
) -> User:
    user = User(
        display_name=name,
        firebase_uid=f'uid-{name}',
        gender=gender,
        push_installations=[PushInstallation(push_token=token)] if token else [],
        registered_at=utc_now(),
    )
    db.add(user)
    db.commit()
    return user


def _invite(db, inviter: User, newbie: User) -> None:
    assert follow_each_other_by_invite(db, newbie, inviter.id) == inviter.id


def test_event_source_covers_public_source():
    """Лог принимает любое значение, которое шлёт клиент, плюс серверное."""
    public = {source.value for source in FollowSource}
    assert public | {'invite'} == {source.value for source in FollowEventSource}


def test_self_referral_creates_no_edges(db):
    newbie = _user(db, 'Newbie')
    attribution = RegistrationAttributionSchema(referrer_id=str(newbie.id))
    assert create_invite_mutual_follow(db, newbie, attribution) is None
    assert db.scalars(select(FollowEvent)).all() == []


def test_existing_edge_is_kept(db):
    """Одно ребро уже есть — создаётся только недостающее, пуш по нему."""
    inviter = _user(db, 'Inviter')
    newbie = _user(db, 'Newbie')
    newbie.follows.append(inviter)
    db.commit()

    _invite(db, inviter, newbie)

    (event,) = db.scalars(select(FollowEvent)).all()
    assert (event.actor_id, event.target_id) == (inviter.id, newbie.id)
    assert event.is_notification_sent is False
    db.refresh(inviter)
    assert newbie in inviter.follows


def test_inviter_already_followed_newbie_no_push_event(db):
    """Подписка пригласившего уже была — события для пуша нет."""
    inviter = _user(db, 'Inviter')
    newbie = _user(db, 'Newbie')
    inviter.follows.append(newbie)
    db.commit()

    _invite(db, inviter, newbie)

    (event,) = db.scalars(select(FollowEvent)).all()
    assert (event.actor_id, event.target_id) == (newbie.id, inviter.id)
    assert event.is_notification_sent is True


def test_db_failure_does_not_raise(db):
    """Ошибка БД (пригласивший исчез) — откат, `None`, регистрация живёт."""
    newbie = _user(db, 'Newbie')
    assert follow_each_other_by_invite(db, newbie, uuid4()) is None
    assert db.scalars(select(FollowEvent)).all() == []
    assert db.get(User, newbie.id) is not None


def test_invite_joined_push(db, fcm):
    inviter = _user(db, 'Inviter', token='token-inviter')
    newbie = _user(db, 'Анна', token='token-newbie', gender=Gender.female)
    _invite(db, inviter, newbie)

    send_new_follower_notifications()

    # Один пуш — пригласившему; новичку «на вас подписались» не шлём.
    (message,) = fcm.messages
    assert message.token == 'token-inviter'
    assert message.android.notification.title == 'Анна присоединилась по вашей ссылке'
    assert message.android.notification.body == 'Теперь вы подписаны друг на друга'
    assert message.data['type'] == 'invite_joined'
    assert message.data['link'] == (
        f'{settings.FRONTEND_URL}/user?userId={newbie.id}&via=push#'
    )
    log = db.scalars(select(PushSendingLog)).one()
    assert log.reason == PushReason.INVITE_JOINED
    assert log.reason_user_id == newbie.id
    assert message.data['delivery_id'] == str(log.id)

    fcm.clear()
    send_new_follower_notifications()
    assert fcm.calls == []


def test_invite_joined_replaces_new_follower_digest(db, fcm):
    """Другой подписчик за тот же час — отдельным дайджестом, без новичка."""
    inviter = _user(db, 'Inviter', token='token-inviter')
    newbie = _user(db, 'Boris', gender=Gender.male)
    other = _user(db, 'Other')
    _invite(db, inviter, newbie)
    other.follows.append(inviter)
    db.add(
        FollowEvent(actor_id=other.id, target_id=inviter.id, action=FollowAction.follow)
    )
    db.commit()

    send_new_follower_notifications()

    titles = sorted(m.android.notification.title for m in fcm.messages)
    assert titles == ['Boris присоединился по вашей ссылке', 'У вас новый подписчик']
    digest = next(m for m in fcm.messages if m.data['type'] == 'new_follower')
    assert digest.android.notification.body == 'На вас подписался Other'


def test_invite_joined_respects_friends_group(db, fcm):
    inviter = _user(db, 'Inviter', token='token-inviter')
    newbie = _user(db, 'Newbie')
    db.add(
        NotificationSetting(
            user_id=inviter.id, group=NotificationGroup.friends, enabled=False
        )
    )
    db.commit()
    _invite(db, inviter, newbie)

    send_new_follower_notifications()

    assert fcm.calls == []
    # Выключенная группа гасит только пуш — рёбра остаются.
    db.refresh(inviter)
    assert newbie in inviter.follows


def test_invite_joined_skipped_when_unfollowed_or_no_push(db, fcm):
    inviter = _user(db, 'Inviter', token='token-inviter')
    silent = _user(db, 'Silent')
    newbie = _user(db, 'Newbie')
    newbie2 = _user(db, 'Newbie2')
    _invite(db, inviter, newbie)
    _invite(db, silent, newbie2)
    # Пригласивший успел отписаться до прогона — «подписаны друг на друга» неправда.
    inviter.follows.remove(newbie)
    db.commit()

    send_new_follower_notifications()

    assert fcm.calls == []
