from app.logging import logger
from app.notifications import (
    send_new_follower_notifications,
    send_reservation_notifincations,
    send_wish_creation_notifications,
)


def main():
    logger.info('Ежечасный крон запущен')
    send_reservation_notifincations()
    send_wish_creation_notifications()
    send_new_follower_notifications()


if __name__ == '__main__':
    main()
