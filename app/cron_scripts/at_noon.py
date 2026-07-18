from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.db import Gender, PushReason, PushSendingLog, SessionLocal, User
from app.firebase import send_push
from app.logging import logger
from app.main import get_user_deep_link
from app.utils import utc_now

# Подписчикам сообщаем, когда до ДР осталось от 3 до 14 дней.
FOLLOWERS_BIRTHDAY_WINDOW_DAYS = (3, 14)
# Самому пользователю напоминаем, когда до ДР осталось менее 21 дня.
CURRENT_USER_BIRTHDAY_NOTIFY_DAYS_IN_ADVANCE = 21
# Не повторять напоминание подписчикам про одного и того же именинника чаще.
NO_REPEAT_FOLLOWERS_PUSH_DAYS = 200


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
                select(User).where(User.birth_date.isnot(None))
            ).all()
            if user.birth_date is not None
            and days_until_next_birthday(user.birth_date)
            < CURRENT_USER_BIRTHDAY_NOTIFY_DAYS_IN_ADVANCE
        ]
    for user in users_with_upcoming_birthday:
        if not user.firebase_push_token:
            continue
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
        )
        push_log = PushSendingLog(
            sent_at=datetime.now(),
            reason=PushReason.CURRENT_USER_BIRTHDAY,
            reason_user_id=user.id,
            target_user_id=user.id,
        )
        with SessionLocal() as db:
            db.add(push_log)
            db.commit()


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
                if not follower.firebase_push_token:
                    continue
                pronoun = 'её' if user.gender == Gender.female else 'его'
                send_push(
                    target_users=[follower],
                    title=f'🎉Скоро день рождения у {user.display_name}!🎉',
                    body=(
                        f'Загляни в {pronoun} хотелки, чтобы '
                        'выбрать идеальный подарок! 🎈'
                    ),
                    link=get_user_deep_link(user),
                )
                # Пишем факт отправки в лог — наблюдаемость follower-ДР-пуша.
                db.add(
                    PushSendingLog(
                        sent_at=datetime.now(),
                        reason=PushReason.FOLLOWER_BIRTHDAY,
                        reason_user_id=user.id,
                        target_user_id=follower.id,
                    )
                )
                sent_any = True
            # Гвард обновляем только если реально хоть кому-то отправили. Иначе у
            # именинника без достижимых подписчиков timestamp сжигался бы вхолостую
            # и блокировал пуш на 200 дней для тех, кто подпишется позже (ещё в окне).
            if sent_any:
                user.pre_bday_push_for_followers_last_sent_at = utc_now()
                db.add(user)
                db.commit()


def is_in_campaign_window(campaign: SeasonalCampaign, today: date) -> bool:
    """Попадает ли `today` в окно `[якорь - window_days, якорь]` кампании.

    Якорь берётся в текущем году `today`; год якоря = `today.year`.
    """
    anchor = date(today.year, campaign.month, campaign.day)
    window_start = anchor - timedelta(days=campaign.window_days)
    return window_start <= today <= anchor


def send_seasonal_notifications(today: date | None = None) -> None:
    """Сезонные глобальные пуши по сегментам кампаний.

    Для каждой активной сегодня кампании и каждого её сегмента выбираем юзеров с
    живым токеном, подходящих под фильтры сегмента и ещё не получавших этот пуш
    в текущем сезоне. Один юзер за сезон получает не более одного пуша на
    сегмент — дедуп свёрнут прямо в запрос через `campaign_key`. `today`
    параметризован ради тестируемости без подмены системного времени.
    """
    today = today or date.today()
    for campaign in SEASONAL_CAMPAIGNS:
        if not is_in_campaign_window(campaign, today):
            continue
        for segment in campaign.segments:
            # Год якоря входит в ключ, чтобы «этот сезон» дедупился корректно.
            campaign_key = f'{campaign.key}-{segment.key}-{today.year}'
            # Кому уже слали этот сегмент в этом сезоне — исключаем в запросе.
            already_sent = select(PushSendingLog.target_user_id).where(
                (PushSendingLog.reason == PushReason.SEASONAL)
                & (PushSendingLog.campaign_key == campaign_key)
            )
            with SessionLocal() as db:
                users = db.scalars(
                    select(User).where(
                        User.firebase_push_token.isnot(None),
                        *segment.filters,
                        User.id.not_in(already_sent),
                    )
                ).all()
            for user in users:
                send_push(
                    target_users=[user],
                    title=segment.title,
                    body=segment.body,
                    link=get_user_deep_link(user),
                )
                push_log = PushSendingLog(
                    sent_at=datetime.now(),
                    reason=PushReason.SEASONAL,
                    # У сезонного пуша нет «виновника»-юзера — ссылаемся на
                    # самого получателя ради NOT NULL на reason_user_id.
                    reason_user_id=user.id,
                    target_user_id=user.id,
                    campaign_key=campaign_key,
                )
                with SessionLocal() as db:
                    db.add(push_log)
                    db.commit()
            logger.info(
                f'Сезонная кампания {campaign_key}: отправлено {len(users)} пушей'
            )


def main():
    logger.info('Запуск полуденного крона')
    send_upcoming_birthday_of_current_user_notification()
    send_upcoming_birthday_of_followed_user_notification()
    send_seasonal_notifications()


if __name__ == '__main__':
    main()
