"""Hawk (hawk.so) — трекер ошибок.

Ошибки уходят в Hawk через сток loguru уровня ERROR, а не ручным `send()` в
каждом месте: так туда попадает всё, что где-либо залогировано как
`error`/`exception`, — и из HTTP-мидлвари, и из джоб планировщика (у него свой
процесс, `app.main` он не импортирует). Без токена `Hawk.handler` — no-op.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from hawk_python_sdk import Hawk

from app.config import settings

if TYPE_CHECKING:
    from loguru import Message

# SDK допускает None в рантайме (no-op), но в их сигнатуре тип занижен.
hawk = Hawk(settings.HAWK_TOKEN)  # ty: ignore[invalid-argument-type]


class LoggedError(Exception):
    """`logger.error(...)` без исключения: в Hawk уходит текст записи."""


def make_hawk_sink(tracker: Hawk) -> 'Callable[[Message], None]':
    def sink(message: 'Message') -> None:
        record = message.record
        context = {
            'logger': record['name'],
            'function': record['function'],
            'line': record['line'],
            'process': record['process'].name,
        }
        exception = record['exception']
        # loguru типизирует type/value как Optional, SDK ждёт строгие типы.
        if (
            exception is not None
            and exception.type is not None
            and isinstance(exception.value, Exception)
        ):
            tracker.handler(
                exception.type, exception.value, exception.traceback, context
            )
        else:
            error = LoggedError(record['message'])
            tracker.handler(LoggedError, error, None, context)

    return sink
