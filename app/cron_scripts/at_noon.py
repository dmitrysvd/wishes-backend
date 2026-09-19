import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import (
    Gender,
    NotificationGroup,
    NotificationSetting,
    PushInstallation,
    PushReason,
    PushSendingLog,
    SessionLocal,
    User,
    Wish,
)
from app.firebase import send_push
from app.logging import logger
from app.main import get_user_deep_link
from app.price_alerts import send_price_alerts
from app.utils import utc_now

# Подписчикам сообщаем, когда до ДР осталось от 3 до 14 дней.
FOLLOWERS_BIRTHDAY_WINDOW_DAYS = (3, 14)
# Самому пользователю напоминаем, когда до ДР осталось менее 21 дня.
CURRENT_USER_BIRTHDAY_NOTIFY_DAYS_IN_ADVANCE = 21
# Не повторять напоминание подписчикам про одного и того же именинника чаще.
NO_REPEAT_FOLLOWERS_PUSH_DAYS = 200
# Не чаще одного реактивационного пуша про пустой список на юзера.
NO_REPEAT_EMPTY_LIST_REACTIVATION_DAYS = 90
# Реактивацию шлём только недавним регистрантам: пока намерение завести список
# свежее (онбординг). Холодное «у тебя пусто» давним неактивным юзерам — высокий
# риск раздражения при низком выхлопе, поэтому их не трогаем.
RECENT_REGISTRANT_DAYS = 30


@dataclass(frozen=True)
class SeasonalSegment:
    """Аудитория внутри кампании: SQLAlchemy-фильтры + своя копия.

    `filters` подставляются прямо в `select(User).where(...)` — таргетинг живёт
    в запросе, а не в Python (эффективнее и композируется с фильтром токена и
    дедупом). Пустой кортеж = «все». Инвариант: фильтры сегментов одной кампании
    должны быть **взаимоисключающими** — иначе юзер попадёт в два сегмента и
    получит два пуша. Сравнение на nullable-поле (напр. `gender == female`) по
    трёхзначной логике SQL само отсекает NULL/unknown — то, что нужно для
    гендерных поводов (никогда не пиши `!= male` как прокси «женский»).

    `key` входит в `campaign_key` лога (`f'{campaign}-{segment}-{year}'`) —
    ключ дедупа на сегмент в пределах сезона.
    """

    key: str
    filters: tuple
    title: str
    body: str


@dataclass(frozen=True)
class SeasonalCampaign:
    """Один сезонный повод: дата-якорь, окно заблаговременности, сегменты.

    `window_days` — сколько дней до якоря (включительно) пуш активен. Инвариант
    на якорь: дата `(month, day)` должна существовать в любом году и окно не
    должно пересекать 1 января — поэтому НГ якорим на 31 декабря, а не на 1
    января (иначе окно ушло бы в прошлый год). 29 февраля как якорь недопустимо.
    """

    key: str
    month: int
    day: int
    window_days: int
    segments: tuple[SeasonalSegment, ...]


# Универсальные (негендерные) сегменты — шлём всем, кто с токеном.
def _all_users_segment(key: str, title: str, body: str) -> SeasonalSegment:
    return SeasonalSegment(key=key, filters=(), title=title, body=body)


# Декларативный список сезонных кампаний. Копии/даты — разумные дефолты
# (открытый продуктовый вопрос Q1 в плане 0002). Гендерные поводы (23 фев / 8
# мар) таргетим ТОЛЬКО по известному полу; unknown-gender в них не попадает.
# Мужчин на 8 марта не трогаем: даритель-реэнгейджмент уже закрыт пушем о
# новых хотелках подписок (`app/notifications.py`).
SEASONAL_CAMPAIGNS: tuple[SeasonalCampaign, ...] = (
    SeasonalCampaign(
        key='new-year',
        month=12,
        day=31,
        window_days=14,
        segments=(
            _all_users_segment(
                'all',
                'Скоро Новый год 🎄',
                'Обнови список желаний — самый подарочный сезон уже близко ✨',
            ),
        ),
    ),
    SeasonalCampaign(
        key='feb14',
        month=2,
        day=14,
        window_days=7,
        segments=(
            _all_users_segment(
                'all',
                'Скоро День святого Валентина 💝',
                'Обнови список желаний, чтобы близкий человек знал, '
                'что тебе будет приятно получить',
            ),
        ),
    ),
    SeasonalCampaign(
        key='feb23',
        month=2,
        day=23,
        window_days=7,
        segments=(
            SeasonalSegment(
                key='male',
                filters=(User.gender == Gender.male,),
                title='Скоро 23 Февраля 🎁',
                body='Обнови список желаний, чтобы близкие знали, что подарить',
            ),
        ),
    ),
    SeasonalCampaign(
        key='mar8',
        month=3,
        day=8,
        window_days=7,
        segments=(
            SeasonalSegment(
                key='female',
                filters=(User.gender == Gender.female,),
                title='Скоро 8 Марта 🌷',
                body='Обнови список желаний, чтобы близкие знали, что подарить ✨',
            ),
        ),
    ),
)


def get_next_birthday(birth_date: date) -> datetime:
    """Ближайшая дата дня рождения (сегодня или в будущем).

    Устойчиво к 29 февраля: в невисокосный год отмечаем 28 февраля.
    """

    def birthday_in(year: int) -> datetime:
        try:
            return datetime(year=year, month=birth_date.month, day=birth_date.day)
        except ValueError:
            # 29 февраля в невисокосный год -> отмечаем 28 февраля.
            return datetime(year=year, month=birth_date.month, day=28)

    now = datetime.now()
    next_birthday = birthday_in(now.year)
    if next_birthday < now:
        next_birthday = birthday_in(now.year + 1)
    return next_birthday


def days_until_next_birthday(birth_date: date) -> int:
    return (get_next_birthday(birth_date) - datetime.now()).days


def send_upcoming_birthday_of_current_user_notification():
    with SessionLocal() as db:
        users_with_upcoming_birthday = [
            user
            for user in db.scalars(
                # Фильтр по установкам — в SQL: дальше юзеры отвязаны от сессии.
                select(User).where(User.birth_date.isnot(None), User.can_receive_push)
            ).all()
            if user.birth_date is not None
            and days_until_next_birthday(user.birth_date)
            < CURRENT_USER_BIRTHDAY_NOTIFY_DAYS_IN_ADVANCE
        ]
    for user in users_with_upcoming_birthday:
        with SessionLocal() as db:
            if db.scalars(
                select(PushSendingLog).where(
                    (PushSendingLog.reason == PushReason.CURRENT_USER_BIRTHDAY)
                    & (PushSendingLog.reason_user_id == user.id)
                    & (PushSendingLog.sent_at > datetime.now() - timedelta(days=30))
                )
            ).first():
                continue
        send_push(
            target_users=[user],
            title='🎉Скоро твой день рождения!🎉',
            body=(
                'Не забудь обновить свои хотелки и поделиться ими с '
                'друзьями и близкими, чтобы они узнали, что ты хочешь '
                'получить в подарок! ✨🎁'
            ),
            reason=PushReason.CURRENT_USER_BIRTHDAY,
        )


def followers_push_recently_sent(last_sent: datetime | None) -> bool:
    if last_sent is None:
        return False
    # Колонка хранит naive-время; приводим к naive на случай aware-значения.
    if last_sent.tzinfo is not None:
        last_sent = last_sent.replace(tzinfo=None)
    return last_sent > datetime.now() - timedelta(days=NO_REPEAT_FOLLOWERS_PUSH_DAYS)


def send_upcoming_birthday_of_followed_user_notification():
    min_days, max_days = FOLLOWERS_BIRTHDAY_WINDOW_DAYS
    with SessionLocal() as db:
        candidates = db.scalars(select(User).where(User.birth_date.isnot(None))).all()
        for user in candidates:
            assert user.birth_date is not None
            if not min_days <= days_until_next_birthday(user.birth_date) <= max_days:
                continue
            if followers_push_recently_sent(
                user.pre_bday_push_for_followers_last_sent_at
            ):
                continue
            sent_any = False
            for follower in user.followed_by:
                if not follower.can_receive_push:
                    continue
                pronoun = 'её' if user.gender == Gender.female else 'его'
                sent = send_push(
                    target_users=[follower],
                    title=f'🎉Скоро день рождения у {user.display_name}!🎉',
                    body=(
                        f'Загляни в {pronoun} хотелки, чтобы '
                        'выбрать идеальный подарок! 🎈'
                    ),
                    reason=PushReason.FOLLOWER_BIRTHDAY,
                    reason_user=user,
                    link=get_user_deep_link(user),
                )
                sent_any = sent_any or bool(sent.sent_user_ids)
            # Гвард обновляем только если реально хоть кому-то отправили (по
            # возврату send_push — подписчик с выключенной группой «Дни рождения»
            # отправкой не считается). Иначе у именинника без достижимых
            # подписчиков timestamp сжигался бы вхолостую и блокировал пуш на 200
            # дней для тех, кто подпишется позже (ещё в окне).
            if sent_any:
                user.pre_bday_push_for_followers_last_sent_at = utc_now()
                db.add(user)
                db.commit()


def send_empty_list_reactivation_notifications():
    """Реактивация пользователей с пустым списком желаний.

    Пустой список — тупик петли дарения: даже пришедший по ссылке даритель не
    видит, что подарить. Деликатно подталкиваем завести хотелки. Критерий
    «пустой список» — нет ни одной НЕ-архивной хотелки (тот же признак, что и у
    публичного вишлиста в `app/routers/public.py`). Шлём только недавним
    регистрантам (`RECENT_REGISTRANT_DAYS`) — онбординг, а не холодная
    реактивация давно неактивных. Дедуп — не чаще одного пуша на юзера в
    `NO_REPEAT_EMPTY_LIST_REACTIVATION_DAYS` дней через `PushSendingLog`.
    """
    with SessionLocal() as db:
        # Недавние регистранты с живым токеном и без единой не-архивной хотелки.
        users_with_empty_list = db.scalars(
            select(User).where(
                User.can_receive_push
                & ~User.wishes.any(~Wish.is_archived)
                & (
                    User.registered_at
                    > datetime.now() - timedelta(days=RECENT_REGISTRANT_DAYS)
                )
            )
        ).all()
    for user in users_with_empty_list:
        with SessionLocal() as db:
            recently_sent = db.scalars(
                select(PushSendingLog).where(
                    (PushSendingLog.reason == PushReason.EMPTY_LIST_REACTIVATION)
                    & (PushSendingLog.target_user_id == user.id)
                    & (
                        PushSendingLog.sent_at
                        > datetime.now()
                        - timedelta(days=NO_REPEAT_EMPTY_LIST_REACTIVATION_DAYS)
                    )
                )
            ).first()
        if recently_sent:
            continue
        send_push(
            target_users=[user],
            # Копия деликатная (черновик, продуктовый вопрос Q1 в плане 0004).
            title='Твой список желаний пуст 🎁',
            body=('Заполни его, чтобы близкие знали, что подарить тебе на праздник'),
            reason=PushReason.EMPTY_LIST_REACTIVATION,
            link=get_user_deep_link(user),
        )


def is_in_campaign_window(campaign: SeasonalCampaign, today: date) -> bool:
    """Попадает ли `today` в окно `[якорь - window_days, якорь]` кампании.

    Якорь берётся в текущем году `today`; год якоря = `today.year`.
    """
    anchor = date(today.year, campaign.month, campaign.day)
    window_start = anchor - timedelta(days=campaign.window_days)
    return window_start <= today <= anchor


def seasonal_campaign_key(
    campaign: SeasonalCampaign, segment: SeasonalSegment, today: date
) -> str:
    """Ключ дедупа сегмента в сезоне. Год якоря входит в ключ, чтобы «этот
    сезон» дедупился корректно."""
    return f'{campaign.key}-{segment.key}-{today.year}'


def select_seasonal_recipients(
    db: Session, campaign: SeasonalCampaign, segment: SeasonalSegment, today: date
) -> list[User]:
    """Боевая выборка получателей сегмента: с живым токеном, под фильтрами
    сегмента, не тестовые и ещё не получавшие этот сегмент в сезоне `today`.

    Дедуп свёрнут прямо в запрос через `campaign_key`. Тестовые аккаунты
    исключены здесь, а не в `send_push`: иначе репетиция на них расходовала бы
    боевой ключ, а боевая рассылка уходила бы на стенд.
    """
    campaign_key = seasonal_campaign_key(campaign, segment, today)
    already_sent = select(PushSendingLog.target_user_id).where(
        (PushSendingLog.reason == PushReason.SEASONAL)
        & (PushSendingLog.campaign_key == campaign_key)
    )
    return list(
        db.scalars(
            select(User).where(
                User.can_receive_push,
                ~User.is_test,
                *segment.filters,
                User.id.not_in(already_sent),
            )
        ).all()
    )


def _active_segments(today: date) -> list[tuple[SeasonalCampaign, SeasonalSegment]]:
    return [
        (campaign, segment)
        for campaign in SEASONAL_CAMPAIGNS
        if is_in_campaign_window(campaign, today)
        for segment in campaign.segments
    ]


def send_seasonal_notifications(today: date | None = None) -> None:
    """Сезонные глобальные пуши по сегментам кампаний.

    Для каждой активной сегодня кампании и каждого её сегмента шлём получателям
    из `select_seasonal_recipients`. Один юзер за сезон получает не более
    одного пуша на сегмент. `today` параметризован ради тестируемости без
    подмены системного времени.
    """
    today = today or date.today()
    for campaign, segment in _active_segments(today):
        campaign_key = seasonal_campaign_key(campaign, segment, today)
        with SessionLocal() as db:
            users = select_seasonal_recipients(db, campaign, segment, today)
        for user in users:
            send_push(
                target_users=[user],
                title=segment.title,
                body=segment.body,
                reason=PushReason.SEASONAL,
                campaign_key=campaign_key,
                link=get_user_deep_link(user),
            )
        logger.info(f'Сезонная кампания {campaign_key}: отправлено {len(users)} пушей')


def seasonal_dry_run(today: date | None = None) -> list[str]:
    """Сухой прогон: кто получил бы сезонный пуш на дату `today` и какой это
    срез — без отправки и без записи в БД (ни лога, ни гвардов).

    Возвращает строки отчёта (они же уходят в лог) — чтобы CLI печатал их, а
    тест проверял без перехвата stdout. `is_test` в срезе — сколько тестовых
    аккаунтов боевая выборка отсекла; остальные счётчики — по получателям.
    """
    today = today or date.today()
    now = utc_now()
    lines: list[str] = []
    active = _active_segments(today)
    if not active:
        lines.append(f'{today}: ни одна сезонная кампания не в окне')
    with SessionLocal() as db:
        for campaign, segment in active:
            campaign_key = seasonal_campaign_key(campaign, segment, today)
            users = select_seasonal_recipients(db, campaign, segment, today)
            user_ids = [u.id for u in users]
            # Свежесть адресата — по самой новой установке юзера.
            token_age_days = {
                user_id: (now - newest).days
                for user_id, newest in db.execute(
                    select(
                        PushInstallation.user_id,
                        func.max(PushInstallation.saved_at),
                    )
                    .where(PushInstallation.user_id.in_(user_ids))
                    .group_by(PushInstallation.user_id)
                ).all()
            }
            tips_disabled = set(
                db.scalars(
                    select(NotificationSetting.user_id).where(
                        NotificationSetting.user_id.in_(user_ids),
                        NotificationSetting.group == NotificationGroup.tips,
                        NotificationSetting.enabled.is_(False),
                    )
                ).all()
            )
            excluded_test = db.scalar(
                select(func.count())
                .select_from(User)
                .where(User.can_receive_push, User.is_test, *segment.filters)
            )
            ages = list(token_age_days.values())
            lines.append(
                f'{campaign_key}: получателей {len(users)}; '
                f'токен <30д: {sum(a < 30 for a in ages)}, '
                f'30–90д: {sum(30 <= a <= 90 for a in ages)}, '
                f'>90д: {sum(a > 90 for a in ages)}; '
                f'birth_date есть: {sum(u.birth_date is not None for u in users)}, '
                f'нет: {sum(u.birth_date is None for u in users)}; '
                f'vk_friends_data есть: '
                f'{sum(u.vk_friends_data is not None for u in users)}, '
                f'нет: {sum(u.vk_friends_data is None for u in users)}; '
                f'выключили tips: {len(tips_disabled)}; '
                f'is_test отсечено: {excluded_test}'
            )
    for line in lines:
        logger.info(f'[dry-run] {line}')
    return lines


def send_seasonal_rehearsal(user_ids: list[UUID], today: date | None = None) -> int:
    """Репетиция: реальная отправка активных на `today` сегментов только
    указанным юзерам, с ключом `<боевой ключ>-rehearsal`.

    Сегментная выборка и дедуп не применяются — получатели ровно те, что
    переданы (в т.ч. `is_test`), повторный запуск шлёт снова. Боевой ключ не
    расходуется: в декабре эти же юзеры получат настоящий пуш.
    Возвращает число отправок.
    """
    today = today or date.today()
    sent = 0
    with SessionLocal() as db:
        users = list(db.scalars(select(User).where(User.id.in_(user_ids))).all())
        missing = set(user_ids) - {u.id for u in users}
        if missing:
            raise SystemExit(f'Юзеры не найдены: {sorted(map(str, missing))}')
        for campaign, segment in _active_segments(today):
            campaign_key = seasonal_campaign_key(campaign, segment, today)
            rehearsal_key = f'{campaign_key}-rehearsal'
            for user in users:
                send_push(
                    target_users=[user],
                    title=segment.title,
                    body=segment.body,
                    reason=PushReason.SEASONAL,
                    campaign_key=rehearsal_key,
                    link=get_user_deep_link(user),
                )
                sent += 1
            logger.info(f'Репетиция {rehearsal_key}: отправлено {len(users)} пушей')
    return sent


def main():
    logger.info('Запуск полуденного крона')
    send_upcoming_birthday_of_current_user_notification()
    send_upcoming_birthday_of_followed_user_notification()
    send_seasonal_notifications()
    # После ночного обхода цен (03:00 UTC) — дайджест по складу (фича 0013).
    send_price_alerts()
    send_empty_list_reactivation_notifications()


def cli(argv: list[str]) -> None:
    """Точка входа скрипта. Без аргументов — обычный полуденный крон; с
    `--seasonal-dry-run` / `--send-to` — только сезонная часть, см. docstring
    `seasonal_dry_run` и `send_seasonal_rehearsal`."""
    parser = argparse.ArgumentParser(description='Полуденный крон')
    parser.add_argument(
        '--seasonal-dry-run',
        action='store_true',
        help='Посчитать получателей сезонных пушей, ничего не слать и не писать',
    )
    parser.add_argument(
        '--send-to',
        type=lambda s: [UUID(x) for x in s.split(',')],
        metavar='UUID[,UUID...]',
        help='Репетиция: реально отправить сезонный пуш этим юзерам с ключом '
        '<боевой ключ>-rehearsal',
    )
    parser.add_argument(
        '--today',
        type=date.fromisoformat,
        help='Дата, на которую считать окно кампаний (по умолчанию сегодня)',
    )
    args = parser.parse_args(argv)
    if args.seasonal_dry_run and args.send_to:
        parser.error('--seasonal-dry-run и --send-to взаимоисключающие')
    if args.seasonal_dry_run:
        for line in seasonal_dry_run(args.today):
            print(line)
    elif args.send_to:
        print(f'Отправлено: {send_seasonal_rehearsal(args.send_to, args.today)}')
    else:
        main()


if __name__ == '__main__':
    cli(sys.argv[1:])
