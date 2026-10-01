from app.db import SessionLocal
from app.guests import delete_orphan_guests
from app.logging import logger
from app.notifications import (
    send_new_follower_notifications,
    send_reservation_notifincations,
    send_wish_creation_notifications,
)
from app.utils import utc_now


def main():
    logger.info('Ежечасный крон запущен')
    send_reservation_notifincations()
    send_wish_creation_notifications()
    send_new_follower_notifications()
    with SessionLocal() as db:
        deleted = delete_orphan_guests(db, utc_now())
    logger.info('Удалено гостей без резервов: {n}', n=deleted)


if __name__ == '__main__':
    main()
