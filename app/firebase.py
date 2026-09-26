from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

import firebase_admin
from firebase_admin import auth, messaging
from firebase_admin.auth import UserRecord
from sqlalchemy import delete, select

from app.config import settings
from app.constants import PriceAlertTrigger
from app.db import PushInstallation, PushReason, PushSendingLog, SessionLocal, User
from app.logging import logger
from app.notification_settings import disabled_user_ids

cred = firebase_admin.credentials.Certificate(settings.FIREBASE_KEY_PATH)

firebase_admin.initialize_app(cred)


@dataclass(frozen=True)
class PushSendOutcome:
    """Итог `send_push`: сколько сообщений ушло и кого FCM принял.

    `sent_user_ids` — адресаты, которым построено сообщение (= строки лога; по
    ним вызывающий решает, расходовать ли свой гвард, напр.
    `pre_bday_push_for_followers_last_sent_at`). Установок у адресата может
    быть несколько — сообщений уходит больше, но единица — юзер;
    `accepted_user_ids` ⊆ `sent_user_ids` — те, у кого FCM принял хотя бы одну
    установку (не «доставлено» — доставку FCM не подтверждает). Мёртвый адрес
    и сбой сюда не попадают.
    """

    sent_user_ids: frozenset[UUID] = field(default_factory=frozenset)
    accepted_user_ids: frozenset[UUID] = field(default_factory=frozenset)


def send_push(
    target_users: list[User],
    title: str,
    body: str,
    *,
    reason: PushReason,
    reason_user: User | None = None,
    campaign_key: str | None = None,
    link: str | None = None,
    trigger: PriceAlertTrigger | None = None,
    with_delivery_id: bool = False,
    kind: str | None = None,
) -> PushSendOutcome:
    """Единственная точка отправки пушей; сама пишет `PushSendingLog`.

    `trigger` — тип триггера пуша по складу (0013), уходит в лог и в
    `data.trigger`. `with_delivery_id` — положить в `data.delivery_id` id
    будущей строки лога: по нему клиент сообщает об открытии
    (`POST /push/opened`), поэтому id генерится ДО отправки. `kind` — вид пуша
    в `data.type` по контракту (`x-push-payload`). `title`/`body`
    дублируются в `data` для тоста в foreground.

    Лог — источник правды для дедупа (крон-пуши читают его перед отправкой) и
    для метрики «пушей на юзера в неделю», поэтому обойти его нельзя: `reason`
    обязателен, а `firebase_admin.messaging` вне этого модуля запрещён линтером
    (`TID251` в `pyproject.toml`). `reason_user` — «виновник» пуша (именинник,
    автор хотелок, новый подписчик); у пуша без виновника — сам получатель.
    Строка лога пишется на каждое построенное сообщение независимо от исхода
    доставки: неудачная доставка не должна перезапускать дедуп.

    Настройки уведомлений (фича 0012) применяются здесь же: юзер, выключивший
    группу `reason`, из адресатов выбывает ДО отправки и ДО записи лога — гварды,
    читающие лог («раз в 30 дней»), при выключенной группе не расходуются.
    """
    if not target_users:
        logger.info('Пустой список получателей. Пуши не отправлены.')
        return PushSendOutcome()
    data = {
        'click_action': 'FLUTTER_NOTIFICATION_CLICK',
        'title': title,
        'body': body,
    }
    if link:
        data['link'] = link
    if trigger is not None:
        data['type'] = 'price_alert'
        data['trigger'] = trigger.value
    if kind is not None:
        data['type'] = kind
    android_notification = messaging.AndroidNotification(
        title=title,
        body=body,
    )
    android_config = messaging.AndroidConfig(notification=android_notification)
    messages = []
    # Параллельно `messages`: чья установка и какой юзер за каждым сообщением.
    message_installations: list[PushInstallation] = []
    users_with_message_ids: list[UUID] = []
    delivery_ids: list[UUID] = []

    target_users = list(set(target_users))
    with SessionLocal() as db:
        opted_out = disabled_user_ids(db, (u.id for u in target_users), reason)
        # Установки читаем своим запросом, а не через `user.push_installations`:
        # адресаты часто приходят отвязанными от сессии (крон), ленивая
        # загрузка на них упала бы.
        installations_by_user: dict[UUID, list[PushInstallation]] = {}
        for installation in db.scalars(
            select(PushInstallation).where(
                PushInstallation.user_id.in_(
                    u.id for u in target_users if u.id not in opted_out
                )
            )
        ):
            installations_by_user.setdefault(installation.user_id, []).append(
                installation
            )
    if opted_out:
        logger.info(
            'Группа пуша {reason} выключена у {count} адресатов, пропущены',
            reason=reason.name,
            count=len(opted_out),
        )
    for user in target_users:
        if user.id in opted_out:
            continue
        installations = installations_by_user.get(user.id)
        if not installations:
            logger.warning(
                'Не отправлено сообщение: у юзера нет установок: {user_id}',
                user_id=user.id,
            )
            continue
        # Один delivery_id (= строка лога) на юзера: дедуп и `push/opened` —
        # на юзера, установки — просто адреса одного и того же пуша.
        delivery_id = uuid4()
        message_data = (
            {**data, 'delivery_id': str(delivery_id)} if with_delivery_id else data
        )
        for installation in installations:
            messages.append(
                messaging.Message(
                    android=android_config,
                    data=message_data,
                    **installation_target(installation),
                )
            )
            message_installations.append(installation)
        users_with_message_ids.append(user.id)
        delivery_ids.append(delivery_id)
    logger.info(
        f'Отправка {len(messages)} сообщений пользователям: {users_with_message_ids}'
    )
    if not messages:
        return PushSendOutcome()
    response = messaging.send_each(messages, dry_run=settings.IS_DEBUG)
    logger.info(
        'Результат отправки пушей: доставлено {success}, провалено {failure}',
        success=response.success_count,
        failure=response.failure_count,
    )
    sent_at = datetime.now()
    with SessionLocal() as db:
        db.add_all(
            PushSendingLog(
                id=delivery_id,
                sent_at=sent_at,
                reason=reason,
                reason_user_id=reason_user.id if reason_user else user_id,
                target_user_id=user_id,
                campaign_key=campaign_key,
                trigger=trigger,
            )
            for user_id, delivery_id in zip(
                users_with_message_ids, delivery_ids, strict=True
            )
        )
        db.commit()
    accepted = frozenset(
        installation.user_id
        for resp, installation in zip(
            response.responses, message_installations, strict=True
        )
        if resp.success
    )
    dead = dead_installation_ids(response.responses, message_installations)
    if dead:
        # Удаляем установки, которые FCM признал недоставляемыми по адресату:
        # мёртв FID или токен — мертва установка целиком.
        with SessionLocal() as db:
            db.execute(delete(PushInstallation).where(PushInstallation.id.in_(dead)))
            db.commit()
        logger.warning(
            'Удалено мёртвых установок: {count}',
            count=len(dead),
        )
    return PushSendOutcome(
        sent_user_ids=frozenset(users_with_message_ids), accepted_user_ids=accepted
    )


class SendResponseLike(Protocol):
    """Структурный контракт ответа FCM: то, что читает разбор доставки."""

    # Только чтение: у `messaging.SendResponse` это свойства без сеттера,
    # с обычными атрибутами протокол с ним не совместим.
    @property
    def success(self) -> bool: ...

    @property
    def exception(self) -> Exception | None: ...


def installation_target(installation: PushInstallation) -> dict[str, str]:
    """Адрес FCM-сообщения для установки: FID, если есть, иначе токен.

    Возвращает kwargs для `messaging.Message` — `{'fid': ...}` либо
    `{'token': ...}`. Откат на токен для всех — заменить тело на
    `{'token': installation.push_token}`.
    """
    if installation.fid:
        return {'fid': installation.fid}
    return {'token': installation.push_token}


_DEAD_ADDRESS_ERRORS = (messaging.UnregisteredError, messaging.SenderIdMismatchError)


def dead_installation_ids(
    responses: Sequence[SendResponseLike],
    installations: Sequence[PushInstallation],
) -> list[UUID]:
    """Отбирает установки с устойчивыми ошибками доставки (мёртвый адрес).

    Транзиентные ошибки (quota/internal) не считаются мёртвыми — установку
    сохраняем.
    """
    dead = []
    for resp, installation in zip(responses, installations, strict=True):
        if not resp.success and isinstance(resp.exception, _DEAD_ADDRESS_ERRORS):
            dead.append(installation.id)
    return dead


def create_firebase_user(
    display_name: str,
    photo_url: str,
    email: str | None,
    phone: str | None,
) -> str:
    user: UserRecord = auth.create_user(
        email=email,
        email_verified=False,
        display_name=display_name,
        photo_url=photo_url,
    )
    return user.uid


def delete_firebase_user(uid: str) -> None:
    auth.delete_user(uid)


def create_custom_firebase_token(uid: str) -> str:
    custom_token = auth.create_custom_token(uid)
    return custom_token.decode()


def get_firebase_user_data(uid: str) -> UserRecord:
    return auth.get_user(uid)
