import pytest
from fastapi.testclient import TestClient

from app.main import app


def test_health(api_client: TestClient):
    response = api_client.get('/health')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


def test_head_supported_by_default(api_client: TestClient):
    # Все GET-роуты должны отвечать на HEAD
    response = api_client.head('/health')
    assert response.status_code == 200


def test_debug_error(api_client: TestClient):
    response = api_client.get('/debug-error')
    assert response.status_code == 200


def test_custom_openapi():
    # Clear cache if any
    app.openapi_schema = None
    openapi = app.openapi()
    assert 'ApiKey' in openapi['components']['securitySchemes']
    assert openapi['security'] == [{'ApiKey': []}]

    # Test caching
    assert app.openapi() is openapi


def test_internal_exception_handler_debug(mocker):
    # When IS_DEBUG is True, it should not call alert_exception
    mocker.patch('app.main.settings.IS_DEBUG', True)
    mock_alert = mocker.patch('app.main.alert_exception')

    client = TestClient(app)

    # We need a route that raises an exception
    @app.get('/error-test')
    async def error_test():
        raise ValueError('test error')

    with pytest.raises(ValueError):
        client.get('/error-test')

    mock_alert.assert_not_called()


def test_internal_exception_handler_no_debug(mocker):
    # When IS_DEBUG is False, it should call alert_exception
    mocker.patch('app.main.settings.IS_DEBUG', False)
    mock_alert = mocker.patch('app.main.alert_exception')

    client = TestClient(app)

    @app.get('/error-test-2')
    async def error_test_2():
        raise ValueError('test error')

    with pytest.raises(ValueError):
        client.get('/error-test-2')

    mock_alert.assert_called_once()
