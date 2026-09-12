import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from loguru import logger

from app.main import internal_exception_handler


@pytest.fixture
def error_records():
    records = []
    handler_id = logger.add(lambda m: records.append(m.record), level='ERROR')
    yield records
    logger.remove(handler_id)


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
async def test_internal_exception_handler_debug_false(mocker, error_records):
    mocker.patch('app.main.settings.IS_DEBUG', False)
    mock_request = mocker.MagicMock(spec=Request)

    async def call_next(request):
        raise ValueError('Test Exception')

    with pytest.raises(ValueError, match='Test Exception'):
        await internal_exception_handler(mock_request, call_next)

    # В Hawk уходит всё уровня ERROR (сток в app/logging.py): проверяем запись.
    (record,) = error_records
    assert isinstance(record['exception'].value, ValueError)
