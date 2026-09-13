"""Настройки уведомлений по группам (фича 0012)."""

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import NotificationGroup
from app.cron_scripts.at_noon import (
    send_upcoming_birthday_of_followed_user_notification,
)
from app.db import (
    NotificationSetting,
    NotificationSettingEvent,
    PushReason,
    PushSendingLog,
    User,
)
from app.firebase import send_push
from app.main import app, get_current_user, get_db
from app.notification_settings import (
    GROUP_TEXTS,
    PUSH_REASON_GROUP,
    set_group_enabled,
)
from app.schemas import NOTIFICATION_GROUPS_EXAMPLE_ALL_ON, NotificationGroupSchema
from app.utils import utc_now

SETTINGS_URL = '/users/me/notification_settings'


def _user(db: Session, name: str = 'Test user', token: str | None = None) -> User:
    user = User(
        display_name=name,
        firebase_uid=f'uid-{name}',
        firebase_push_token=token,
        registered_at=utc_now(),
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def user(db: Session) -> User:
    return _user(db)


@pytest.fixture
def client(db: Session, user: User) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    yield TestClient(app)
    app.dependency_overrides = {}


def _events(db: Session) -> list[NotificationSettingEvent]:
    return list(
        db.scalars(
            select(NotificationSettingEvent).order_by(
                NotificationSettingEvent.created_at
            )
        )
    )


# --- Инварианты продукта ---


def test_every_push_reason_has_group():
    # Тип пуша без группы — ошибка продукта, а не «шлём по умолчанию».
    assert set(PUSH_REASON_GROUP) == set(PushReason)


def test_group_texts_fit_contract():
    # Тексты — те, что в examples контракта, и в лимитах схемы (title ≤30,
    # subtitle ≤45): фронт верстает одну строку по ним.
    for group, example in zip(
        NotificationGroup, NOTIFICATION_GROUPS_EXAMPLE_ALL_ON, strict=True
    ):
        texts = GROUP_TEXTS[group]
        NotificationGroupSchema(
            key=group.value, title=texts.title, subtitle=texts.subtitle, enabled=True
        )
        assert example == {
            'key': group.value,
            'title': texts.title,
            'subtitle': texts.subtitle,
            'enabled': True,
        }


# --- GET ---


def test_read_defaults_all_enabled(client: TestClient):
    response = client.get(SETTINGS_URL)
    assert response.status_code == 200
    assert response.json()['groups'] == NOTIFICATION_GROUPS_EXAMPLE_ALL_ON


# --- PUT ---


def test_toggle_off_then_on(client: TestClient, db: Session, user: User):
    response = client.put(f'{SETTINGS_URL}/friends', json={'enabled': False})
    assert response.status_code == 200
    by_key = {g['key']: g['enabled'] for g in response.json()['groups']}
    assert by_key == {
        'reservation': True,
        'friends': False,
        'birthdays': True,
        'prices': True,
        'tips': True,
    }
    # Повтор того же значения — безвреден и событие не пишет.
    client.put(f'{SETTINGS_URL}/friends', json={'enabled': False})
    assert [(e.group, e.enabled) for e in _events(db)] == [
        (NotificationGroup.friends, False)
    ]

    response = client.put(f'{SETTINGS_URL}/friends', json={'enabled': True})
    assert response.json()['groups'] == NOTIFICATION_GROUPS_EXAMPLE_ALL_ON
    assert [(e.group, e.enabled) for e in _events(db)] == [
        (NotificationGroup.friends, False),
        (NotificationGroup.friends, True),
    ]
    # Одна строка на (юзер, группа): last-write-wins без версий.
    assert (
        len(
            db.scalars(
                select(NotificationSetting).where(
                    NotificationSetting.user_id == user.id
                )
            ).all()
        )
        == 1
    )


def test_toggle_on_when_never_saved_writes_no_event(client: TestClient, db: Session):
    # Дефолт уже «включено» — включение ничего не меняет.
    client.put(f'{SETTINGS_URL}/tips', json={'enabled': True})
    assert _events(db) == []


def test_toggle_unknown_group_is_422(client: TestClient, db: Session):
    # Группы, которой бэк не отдаёт, — невалидный путь (старый клиент с
    # незнакомым key сюда не попадает: он шлёт только присланные значения).
    response = client.put(f'{SETTINGS_URL}/digest', json={'enabled': False})
    assert response.status_code == 422
    # Форма — как у HTTPValidationError FastAPI: клиент разбирает единообразно.
    assert response.json()['detail'][0]['loc'] == ['path', 'group']
    assert db.scalars(select(NotificationSetting)).first() is None


def test_toggle_without_body_is_422(client: TestClient):
    assert client.put(f'{SETTINGS_URL}/friends').status_code == 422


# --- Отсечка в send_push ---


def test_send_push_skips_opted_out_and_keeps_others(db: Session, fcm):
    opted_out = _user(db, 'Off', token='token-off')
    listener = _user(db, 'On', token='token-on')
    set_group_enabled(db, opted_out, NotificationGroup.tips, False)

    sent = send_push([opted_out, listener], 'title', 'body', reason=PushReason.SEASONAL)

    assert sent.sent == 1
    assert sent.accepted_user_ids == {listener.id}
    assert fcm.tokens == ['token-on']
    # Лог — только по реально отправленному: гварды по логу не расходуются.
    assert [log.target_user_id for log in db.scalars(select(PushSendingLog))] == [
        listener.id
    ]


def test_send_push_other_group_still_delivered(db: Session, fcm):
    # Выключен «tips», а пуш — из «reservation»: доходит.
    user = _user(db, 'Partial', token='token')
    set_group_enabled(db, user, NotificationGroup.tips, False)

    assert send_push([user], 't', 'b', reason=PushReason.RESERVATION).sent == 1
    assert fcm.tokens == ['token']


def test_send_push_all_opted_out_returns_zero(db: Session, fcm):
    user = _user(db, 'Off', token='token')
    set_group_enabled(db, user, NotificationGroup.birthdays, False)

    assert send_push([user], 't', 'b', reason=PushReason.FOLLOWER_BIRTHDAY).sent == 0
    assert fcm.calls == []


def test_followers_birthday_guard_not_consumed_when_follower_opted_out(
    db: Session, mocker, fcm
):
    # Единственный подписчик выключил «Дни рождения» → гвард именинника не
    # сжигается: включит обратно в окне — получит пуш этого года.
    mocker.patch(
        'app.cron_scripts.at_noon.get_user_deep_link', return_value='http://link'
    )
    followed = User(
        display_name='Followed',
        firebase_uid='followed_uid',
        birth_date=date.today() + timedelta(days=10),
        registered_at=utc_now(),
    )
    follower = _user(db, 'Follower', token='token_follower')
    follower.follows.append(followed)
    db.add(followed)
    db.commit()
    set_group_enabled(db, follower, NotificationGroup.birthdays, False)

    send_upcoming_birthday_of_followed_user_notification()

    assert fcm.calls == []
    db.refresh(followed)
    assert followed.pre_bday_push_for_followers_last_sent_at is None

    set_group_enabled(db, follower, NotificationGroup.birthdays, True)
    send_upcoming_birthday_of_followed_user_notification()

    assert fcm.tokens == ['token_follower']
    db.refresh(followed)
    assert followed.pre_bday_push_for_followers_last_sent_at is not None
