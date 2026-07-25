"""Heartbeat-отметки фоновых процессов.

Фоновые задачи (ночной бэкап, планировщик) не слушают порт — снаружи их не
опросишь, и молчаливая смерть такой задачи ничем себя не проявляет. Поэтому
задача сама отмечается в файле при каждом успешном прогоне, а приложение отдаёт
свежесть отметки обычной HTTP-ручкой (`/health/heartbeat/{name}`). Так внешний
uptime-монитор ловит зависший процесс тем же механизмом, что и падение сайта.
"""

import time
from pathlib import Path


def touch(name: str, directory: Path) -> None:
    """Отметить успешный прогон процесса `name`."""
    directory.mkdir(exist_ok=True, parents=True)
    (directory / name).touch()


def age_seconds(name: str, directory: Path) -> float | None:
    """Возраст отметки в секундах. None — отметки нет вовсе."""
    path = directory / name
    if not path.exists():
        return None
    return time.time() - path.stat().st_mtime
