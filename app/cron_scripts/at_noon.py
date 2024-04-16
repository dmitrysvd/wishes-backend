from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.db import PushReason, PushSendingLog, SessionLocal, User
from app.firebase import send_push

UPCOMING_BIRTHDAY_NOTIFY_FOLLOWERS_DAYS_IN_ADVANCE = 7
UPCOMING_BIRTHDAY_NOTIFY_CURRENT_USER_IN_ADVANCE = 14


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
            push_tokens=[user.firebase_push_token],
            title='🎉Скоро твой день рождения!🎉',
            body=(
                'Не забудь обновить свои хотелки, чтобы друзья узнали, что ты хочешь получить в подарок!🎁'
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


def main():
    send_upcoming_birthday_of_current_user_notification()


if __name__ == '__main__':
    main()
