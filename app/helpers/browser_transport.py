"""httpx-транспорт поверх curl_cffi с TLS-отпечатком Chrome.

WAF WB (`server: wbaas`) фильтрует по отпечатку TLS-рукопожатия (JA3):
отпечаток питоновского `ssl` + httpx у него в чёрном списке, заголовки не
влияют — 403 приходит до первого байта HTTP. curl_cffi воспроизводит
ClientHello Chrome, и запрос выглядит как браузерный. Интерфейс httpx
сохранён, чтобы код обхода и его тесты на `httpx.MockTransport` не зависели от
библиотеки под капотом.
"""

from typing import cast

import httpx
from curl_cffi import requests as curl_requests
from curl_cffi.requests.session import HttpMethod

# Профиль curl_cffi без версии — библиотека подставляет актуальный Chrome.
IMPERSONATE_BROWSER = 'chrome'
# Заголовки, которые httpx.Client ставит сам. Их не пробрасываем: curl_cffi
# подставляет браузерные, согласованные с отпечатком (иначе UA python-httpx).
HTTPX_DEFAULT_HEADERS = frozenset(
    {'host', 'accept', 'accept-encoding', 'connection', 'user-agent'}
)


class BrowserTransport(httpx.BaseTransport):
    def __init__(self, timeout: float) -> None:
        self._session = curl_requests.Session(
            impersonate=IMPERSONATE_BROWSER, timeout=timeout
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        headers = {
            name: value
            for name, value in request.headers.items()
            if name.lower() not in HTTPX_DEFAULT_HEADERS
        }
        response = self._session.request(
            cast(HttpMethod, request.method),
            str(request.url),
            headers=headers,
            data=request.content or None,
        )
        return httpx.Response(
            status_code=response.status_code,
            headers=list(response.headers.multi_items()),
            content=response.content,
            request=request,
        )

    def close(self) -> None:
        self._session.close()
