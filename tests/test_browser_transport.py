import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from app.helpers.browser_transport import BrowserTransport


@pytest.fixture
def local_server():
    """Настоящий HTTP-сервер в потоке: транспорт проверяется без моков."""
    seen: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append({k.lower(): v for k, v in self.headers.items()})
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', seen
    server.shutdown()


def test_browser_transport_roundtrip(local_server):
    base_url, seen = local_server

    with httpx.Client(transport=BrowserTransport(timeout=5)) as client:
        response = client.get(f'{base_url}/cards', params={'nm': '1;2'})

    assert response.status_code == 200
    assert response.json() == {'ok': True}
    assert response.request.url.params['nm'] == '1;2'
    # Запрос ушёл под личиной браузера, а не python-httpx.
    assert 'chrome' in seen[0]['user-agent'].lower()
