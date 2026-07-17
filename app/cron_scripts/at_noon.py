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


def main():
    logger.info('Запуск полуденного крона')
    send_upcoming_birthday_of_current_user_notification()
    send_upcoming_birthday_of_followed_user_notification()


if __name__ == '__main__':
    main()
