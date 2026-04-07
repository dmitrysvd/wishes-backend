from unittest.mock import MagicMock

import pytest

from app.cron_scripts.every_hour import main as every_hour_main
from app.cron_scripts.every_minute import check_cpu_usage
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


@pytest.mark.anyio
async def test_every_minute_check_cpu_usage_alert(mocker):
    mocker.patch('psutil.cpu_percent', return_value=95.0)
    mock_virtual_memory = MagicMock()
    mock_virtual_memory.percent = 95.0
    mocker.patch('psutil.virtual_memory', return_value=mock_virtual_memory)
    mock_alert = mocker.patch('app.cron_scripts.every_minute.alert_warning')

    mocker.patch('app.config.settings.CPU_ALERT_TRESHOLD', 80)
    mocker.patch('app.config.settings.RAM_ALERT_TRESHOLD', 80)

    await check_cpu_usage()

    assert mock_alert.call_count == 2
    mock_alert.assert_any_call('CPU usage: 95.0%')
    mock_alert.assert_any_call('RAM usage: 95.0%')


@pytest.mark.anyio
async def test_every_minute_check_cpu_usage_no_alert(mocker):
    mocker.patch('psutil.cpu_percent', return_value=10.0)
    mock_virtual_memory = MagicMock()
    mock_virtual_memory.percent = 10.0
    mocker.patch('psutil.virtual_memory', return_value=mock_virtual_memory)
    mock_alert = mocker.patch('app.cron_scripts.every_minute.alert_warning')

    mocker.patch('app.config.settings.CPU_ALERT_TRESHOLD', 80)
    mocker.patch('app.config.settings.RAM_ALERT_TRESHOLD', 80)

    await check_cpu_usage()

    mock_alert.assert_not_called()


def test_every_minute_main():
    # It currently does nothing, but for coverage
    every_minute_main()
