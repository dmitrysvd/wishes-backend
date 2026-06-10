from fastapi import Request

from app.alerts import alert_exception, alert_warning, send_tg_channel_message


def test_send_tg_channel_message(mocker):
    # Mock httpx.Client and its context manager
    mock_client_class = mocker.patch('httpx.Client')
    mock_client = mock_client_class.return_value.__enter__.return_value

    mock_response = mocker.Mock()
    mock_response.raise_for_status = mocker.Mock()
    mock_client.post.return_value = mock_response

    send_tg_channel_message('test message')

    mock_client.post.assert_called_once()
    args, kwargs = mock_client.post.call_args
    assert kwargs['json']['text'] == 'test message'
    mock_response.raise_for_status.assert_called_once()


def test_alert_warning(mocker):
    mock_send = mocker.patch('app.alerts.send_tg_channel_message')
    alert_warning('warn')
    mock_send.assert_called_once_with('Warning:\n\nwarn')


def test_alert_exception(mocker):
    mock_send = mocker.patch('app.alerts.send_tg_channel_message')

    # Mocking Request
    mock_request = mocker.Mock(spec=Request)
    mock_request.url = 'http://test'

    try:
        raise ValueError('test error')
    except ValueError as e:
        alert_exception(mock_request, e)

    mock_send.assert_called_once()
    call_args = mock_send.call_args[0][0]
    assert 'http://test' in call_args
    assert 'ValueError' in call_args
    assert 'test error' in call_args
