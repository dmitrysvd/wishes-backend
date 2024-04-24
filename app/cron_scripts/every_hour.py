from app.notifications import (
    send_reservation_notifincations,
    send_wish_creation_notifications,
)


def main():
    send_reservation_notifincations()
    send_wish_creation_notifications()


if __name__ == '__main__':
    main()
