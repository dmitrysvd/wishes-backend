from datetime import timedelta

from sqlalchemy import select, update

from app.alerts import alert_warning
from app.config import settings
from app.constants import Gender
from app.db import SessionLocal, User, Wish
from app.firebase import send_push
from app.logging import logger
from app.main import get_user_deep_link
from app.utils import utc_now


def send_reservation_notifincations():
    with SessionLocal() as db:
        users_with_reserved_wishes_q = select(User).where(
            User.wishes.any(
                Wish.reserved_by_id.is_not(None)
                & ~Wish.is_reservation_notification_sent
            )
        )
        users = db.scalars(users_with_reserved_wishes_q).all()
    push_tokens = [
        user.firebase_push_token for user in users if user.firebase_push_token
    ]
    with SessionLocal() as db:
        db.execute(
            update(Wish)
            .where(
                Wish.id.in_(
                    select(Wish.id)
                    .join(Wish.user)
                    .where(User.firebase_push_token.in_(push_tokens))
                )
            )
            .values(is_reservation_notification_sent=True)
        )
        db.commit()
    send_push(
        push_tokens=push_tokens,
        title='Кто-то хочет сделать Вам подарок!',
        body=f'Одно из ваших желаний было зарезервировано',
    )


def send_wish_creation_notifications():
    """Отправить всем подписчикам уведомление о новых хотелках."""
    created_not_later_than = utc_now() - timedelta(minutes=30)
    with SessionLocal() as db:
        wishes_filter_cond = ~Wish.is_creation_notification_sent & (
            Wish.created_at < created_not_later_than
        )
        users_q = select(User).join(User.wishes).where(wishes_filter_cond)
        users_with_new_wishes = db.scalars(users_q).all()
        db.execute(
            update(Wish)
            .where(wishes_filter_cond)
            .values(is_creation_notification_sent=True)
        )
        db.commit()
        for user in users_with_new_wishes:
            followers_to_send_push = [
                follower
                for follower in user.followed_by
                if follower.firebase_push_token
            ]
            if followers_to_send_push:
                logger.info(
                    'Отправляются сообщения о создании хотелок: source={user_id}, dest={dest}',
                    user_id=user.id,
                    dest=[str(user.id) for user in followers_to_send_push],
                )
                verb = 'обновила' if user.gender == Gender.female else 'обновил'
                followers_push_tokens = [
                    follower.firebase_push_token
                    for follower in followers_to_send_push
                    if follower.firebase_push_token
                ]
                send_push(
                    push_tokens=followers_push_tokens,
                    title=f'{user.display_name} {verb} список желаний',
                    body=f'Узнайте, что {user.display_name} хочет получить в подарок',
                    link=get_user_deep_link(user),
                )
