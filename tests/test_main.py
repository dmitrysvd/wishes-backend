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
    # Эндпоинт намеренно кидает ошибку; middleware её ре-райзит.
    with pytest.raises(RuntimeError, match='Hawk debug error'):
        api_client.get('/debug-error')


def test_custom_openapi():
    # Clear cache if any
    app.openapi_schema = None
    openapi = app.openapi()
    assert 'ApiKey' in openapi['components']['securitySchemes']
    assert openapi['security'] == [{'ApiKey': []}]

    # Test caching
    assert app.openapi() is openapi


def test_internal_exception_handler_debug(mocker):
    # При IS_DEBUG=True ошибка в трекер не отправляется
    mocker.patch('app.main.settings.IS_DEBUG', True)
    mock_hawk = mocker.patch('app.main.hawk')

    client = TestClient(app)

    # Нужен роут, который кидает исключение
    @app.get('/error-test')
    async def error_test():
        raise ValueError('test error')

    with pytest.raises(ValueError):
        client.get('/error-test')

    mock_hawk.send.assert_not_called()


def test_internal_exception_handler_no_debug(mocker):
    # При IS_DEBUG=False ошибка уходит в трекер
    mocker.patch('app.main.settings.IS_DEBUG', False)
    mock_hawk = mocker.patch('app.main.hawk')

    client = TestClient(app)

    @app.get('/error-test-2')
    async def error_test_2():
        raise ValueError('test error')

    with pytest.raises(ValueError):
        client.get('/error-test-2')

    mock_hawk.send.assert_called_once()


def test_internal_exception_handler_hawk_failure(mocker):
    # Сбой трекера не должен ломать обработку: исходное исключение долетает
    mocker.patch('app.main.settings.IS_DEBUG', False)
    mock_hawk = mocker.patch('app.main.hawk')
    mock_hawk.send.side_effect = RuntimeError('hawk down')

    client = TestClient(app)

    @app.get('/error-test-3')
    async def error_test_3():
        raise ValueError('test error')

    with pytest.raises(ValueError):
        client.get('/error-test-3')
