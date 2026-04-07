import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.main import internal_exception_handler


def test_health(api_client: TestClient):
    response = api_client.get('/health')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


def test_openapi_schema(api_client: TestClient):
    response = api_client.get('/openapi.json')
    assert response.status_code == 200
    schema = response.json()
    assert 'ApiKey' in schema['components']['securitySchemes']
    assert schema['security'] == [{'ApiKey': []}]


@pytest.mark.anyio
async def test_internal_exception_handler_debug_true(mocker):
    mocker.patch('app.main.settings.IS_DEBUG', True)
    mock_request = mocker.MagicMock(spec=Request)

    async def call_next(request):
        raise ValueError('Test Exception')

    with pytest.raises(ValueError, match='Test Exception'):
        await internal_exception_handler(mock_request, call_next)


@pytest.mark.anyio
async def test_internal_exception_handler_debug_false(mocker):
    mocker.patch('app.main.settings.IS_DEBUG', False)
    mock_alert = mocker.patch('app.main.alert_exception')
    mock_request = mocker.MagicMock(spec=Request)

    async def call_next(request):
        raise ValueError('Test Exception')

    with pytest.raises(ValueError, match='Test Exception'):
        await internal_exception_handler(mock_request, call_next)

    mock_alert.assert_called_once()
    assert mock_alert.call_args.args[0] is mock_request
