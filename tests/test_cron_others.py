import pytest

from app.cron_scripts.every_hour import main as every_hour_main
from app.cron_scripts.every_minute import main as every_minute_main


def test_every_hour_main(mocker):
    mock_res = mocker.patch(
        'app.cron_scripts.every_hour.send_reservation_notifincations'
    )
    mock_wish = mocker.patch(
        'app.cron_scripts.every_hour.send_wish_creation_notifications'
    )

    every_hour_main()

    mock_res.assert_called_once()
    mock_wish.assert_called_once()


def test_every_minute_main():
    # It currently does nothing (no-op placeholder), but call it for coverage
    every_minute_main()


def test_every_hour_main_exception(mocker):
    # Coverage for error handling if any, though every_hour main is simple
    mocker.patch(
        'app.cron_scripts.every_hour.send_reservation_notifincations',
        side_effect=Exception('hour error'),
    )
    with pytest.raises(Exception, match='hour error'):
        every_hour_main()


def test_scripts_main_execution(mocker):
    import os
    import runpy

    from app.cron_scripts import every_hour, every_minute

    mocker.patch('app.cron_scripts.every_hour.main')
    script_path_hour = os.path.abspath(every_hour.__file__)
    runpy.run_path(script_path_hour, run_name='__main__')

    mocker.patch('app.cron_scripts.every_minute.main')
    script_path_minute = os.path.abspath(every_minute.__file__)
    runpy.run_path(script_path_minute, run_name='__main__')
