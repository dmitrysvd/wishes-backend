from unittest.mock import AsyncMock

import pytest
from fastapi import Request

from app.admin.setup import AdminAuth


@pytest.mark.anyio
async def test_admin_auth_login_success(mocker):
    mocker.patch('app.admin.setup.settings.ADMIN_PASSWORD', 'correct_password')
    auth = AdminAuth(secret_key='secret')

    # Mock request.form()
    mock_request = mocker.Mock(spec=Request)
    mock_request.form = AsyncMock(
        return_value={'username': 'admin', 'password': 'correct_password'}
    )
    mock_request.session = {}

    result = await auth.login(mock_request)
    assert result is True
    assert mock_request.session.get('has_admin_access') is True


@pytest.mark.anyio
async def test_admin_auth_login_fail(mocker):
    mocker.patch('app.admin.setup.settings.ADMIN_PASSWORD', 'correct_password')
    auth = AdminAuth(secret_key='secret')

    mock_request = mocker.Mock(spec=Request)
    mock_request.form = AsyncMock(
        return_value={'username': 'admin', 'password': 'wrong_password'}
    )
    mock_request.session = {}

    result = await auth.login(mock_request)
    assert result is False
    assert 'has_admin_access' not in mock_request.session


@pytest.mark.anyio
async def test_admin_auth_logout(mocker):
    auth = AdminAuth(secret_key='secret')
    mock_request = mocker.Mock(spec=Request)
    mock_request.session = {'has_admin_access': True}

    result = await auth.logout(mock_request)
    assert result is True
    assert mock_request.session == {}


@pytest.mark.anyio
async def test_admin_auth_authenticate(mocker):
    auth = AdminAuth(secret_key='secret')

    # Authorized
    mock_request_ok = mocker.Mock(spec=Request)
    mock_request_ok.session = {'has_admin_access': True}
    assert await auth.authenticate(mock_request_ok) is True

    # Unauthorized
    mock_request_fail = mocker.Mock(spec=Request)
    mock_request_fail.session = {}
    assert await auth.authenticate(mock_request_fail) is False
