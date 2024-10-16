from app.logging import logger
from app.notifications import (
    send_reservation_notifincations,
    send_wish_creation_notifications,
)


def main():
    logger.info('Ежечасный крон запущен')
    send_reservation_notifincations()
    send_wish_creation_notifications()


if __name__ == '__main__':
    main()
