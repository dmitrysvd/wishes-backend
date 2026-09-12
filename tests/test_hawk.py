import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from hawk_python_sdk import Hawk
from loguru import logger

from app.hawk import LoggedError, make_hawk_sink


@pytest.fixture
def collector():
    """Настоящий HTTP-коллектор в потоке: принимает события Hawk без моков."""
    events: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers['Content-Length'])
            events.append(json.loads(self.rfile.read(length)))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{}')

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', events
    server.shutdown()


@pytest.fixture
def sink_logger(collector):
    """Логгер с Hawk-стоком, направленным в локальный коллектор."""
    endpoint, events = collector
    tracker = Hawk({'token': 'test-token', 'collector_endpoint': endpoint})  # ty: ignore[invalid-argument-type]
    handler_id = logger.add(make_hawk_sink(tracker), level='ERROR')
    yield events
    logger.remove(handler_id)


def test_sink_sends_exception_with_backtrace(sink_logger):
    try:
        raise ValueError('boom')
    except ValueError:
        logger.exception('Упало')

    (event,) = sink_logger
    payload = event['payload']
    assert event['token'] == 'test-token'
    assert payload['type'] == 'ValueError'
    assert payload['title'] == 'ValueError: boom'
    assert payload['backtrace']
    assert payload['context']['function'] == 'test_sink_sends_exception_with_backtrace'


def test_sink_sends_plain_error_as_logged_error(sink_logger):
    logger.error('Обход цен не дал ни одного наблюдения')

    (event,) = sink_logger
    payload = event['payload']
    assert payload['type'] == LoggedError.__name__
    assert payload['title'].endswith(
        'LoggedError: Обход цен не дал ни одного наблюдения'
    )
    assert payload['context']['logger'] == __name__


def test_sink_ignores_levels_below_error(sink_logger):
    logger.warning('батч пропущен')

    assert sink_logger == []


def test_sink_without_token_is_noop():
    tracker = Hawk(None)  # ty: ignore[invalid-argument-type]
    handler_id = logger.add(make_hawk_sink(tracker), level='ERROR')
    try:
        logger.error('в никуда')  # не должно ни упасть, ни ходить в сеть
    finally:
        logger.remove(handler_id)


def test_sink_survives_collector_down():
    # Коллектор недоступен: SDK глотает сетевую ошибку, логирование не ломается.
    tracker = Hawk({'token': 'test-token', 'collector_endpoint': 'http://127.0.0.1:9'})  # ty: ignore[invalid-argument-type]
    handler_id = logger.add(make_hawk_sink(tracker), level='ERROR')
    try:
        logger.error('коллектор лежит')
    finally:
        logger.remove(handler_id)
