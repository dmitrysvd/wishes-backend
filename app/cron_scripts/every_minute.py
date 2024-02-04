import asyncio

import psutil
from sqlalchemy import select, update

from app.alerts import alert_warning
from app.config import settings
from app.db import SessionLocal, User, Wish
from app.firebase import send_pushes
from app.main import logger


async def check_cpu_usage():
    cpu_percent = psutil.cpu_percent()
    if cpu_percent > settings.CPU_ALERT_TRESHOLD:
        await alert_warning(f'CPU usage: {cpu_percent}%')
    memory_usage = psutil.virtual_memory()
    if memory_usage.percent > settings.RAM_ALERT_TRESHOLD:
        await alert_warning(f'RAM usage: {memory_usage.percent}%')


def send_reservation_notifincations():
    with SessionLocal() as db:
        users_with_reserved_wishes_q = select(User).where(
            User.wishes.any(
                Wish.reserved_by_id.is_not(None)
                & ~Wish.is_reservation_notification_sent
            )
        )
        users = db.scalars(users_with_reserved_wishes_q).all()
        print(users)
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
    send_pushes(
        push_tokens=push_tokens,
        title='Кто-то хочет сделать Вам подарок!',
        body=f'Одно из ваших желаний было зарезервировано',
    )


def main():
    asyncio.run(check_cpu_usage())
    send_reservation_notifincations()


if __name__ == '__main__':
    main()
