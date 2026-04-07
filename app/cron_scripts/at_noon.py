from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from app.db import Gender, PushReason, PushSendingLog, SessionLocal, User
from app.firebase import send_push
from app.logging import logger
from app.main import get_user_deep_link
from app.utils import utc_now

UPCOMING_BIRTHDAY_NOTIFY_FOLLOWERS_DAYS_IN_ADVANCE = 7
UPCOMING_BIRTHDAY_NOTIFY_CURRENT_USER_IN_ADVANCE = 21


def get_next_birthday(birth_date: date) -> datetime:
    next_birthday = datetime(
        year=datetime.now().year,
        month=birth_date.month,
        day=birth_date.day,
    )
    if next_birthday < datetime.now():
        next_birthday += timedelta(days=365)
    return next_birthday


def send_upcoming_birthday_of_current_user_notification():
    users_with_upcoming_birthday: list[User] = []
    with SessionLocal() as db:
        for user in db.scalars(select(User).where(User.birth_date.isnot(None))).all():
            assert user.birth_date is not None
            tdelta = timedelta(days=UPCOMING_BIRTHDAY_NOTIFY_CURRENT_USER_IN_ADVANCE)
            next_birthday = get_next_birthday(user.birth_date)
            if next_birthday - datetime.now() < tdelta:
                users_with_upcoming_birthday.append(user)
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


def get_upcoming_birthday_users_condition_q(min_days: int, max_days: int):
    today = date.today()
    lower_limit = today + timedelta(days=min_days)
    upper_limit = today + timedelta(days=max_days)
    current_year = today.year
    next_year = current_year + 1

    bday_1 = func.concat(current_year, func.strftime('-%m-%d', User.birth_date))
    bday_2 = func.concat(next_year, func.strftime('-%m-%d', User.birth_date))

    condition_q = bday_1.between(lower_limit, upper_limit) | bday_2.between(
        lower_limit, upper_limit
    )
    return condition_q


def get_no_repeat_push_condition(min_days_since):
    # TODO: поддерживать другие поля
    return User.pre_bday_push_for_followers_last_sent_at.is_(None) | (
        User.pre_bday_push_for_followers_last_sent_at
        < datetime.now() - timedelta(days=200)
    )


def send_upcoming_birthday_of_followed_user_notification():
    with SessionLocal() as db:
        q = select(User).where(
            get_upcoming_birthday_users_condition_q(3, 14)
            & get_no_repeat_push_condition(210)
        )
        users = db.scalars(q).all()
        for user in users:
            user.pre_bday_push_for_followers_last_sent_at = utc_now()
            db.add(user)
            db.commit()
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


def main():
    logger.info('Запуск полуденного крона')
    send_upcoming_birthday_of_current_user_notification()
    send_upcoming_birthday_of_followed_user_notification()


if __name__ == '__main__':
    main()
