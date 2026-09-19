import os
import time

import pytest
from fastapi.testclient import TestClient

from app import heartbeat
from app.config import settings
from app.main import HEARTBEAT_MAX_AGE_SECONDS, HeartbeatName, app, get_heartbeats_dir


@pytest.fixture
def heartbeats_dir(tmp_path):
    # Подменяем каталог отметок зависимостью, а не моком ФС: ручка работает с
    # настоящими файлами в tmp_path.
    directory = tmp_path / 'heartbeats'
    app.dependency_overrides[get_heartbeats_dir] = lambda: directory
    yield directory
    app.dependency_overrides.pop(get_heartbeats_dir, None)


def test_touch_creates_dir_and_file(tmp_path):
    directory = tmp_path / 'heartbeats'
    heartbeat.touch('backup', directory)
    assert (directory / 'backup').exists()


def test_touch_is_idempotent(tmp_path):
    # Повторная отметка не падает на существующем каталоге и файле.
    heartbeat.touch('backup', tmp_path)
    heartbeat.touch('backup', tmp_path)
    assert (tmp_path / 'backup').exists()


def test_age_seconds_missing(tmp_path):
    assert heartbeat.age_seconds('backup', tmp_path) is None


def test_age_seconds_fresh(tmp_path):
    heartbeat.touch('backup', tmp_path)
    age = heartbeat.age_seconds('backup', tmp_path)
    assert age is not None
    assert age < 5


def test_endpoint_fresh(heartbeats_dir, api_client: TestClient):
    heartbeat.touch('backup', heartbeats_dir)
    response = api_client.get('/health/heartbeat/backup')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok', 'age_seconds': 0}


def test_endpoint_missing(heartbeats_dir, api_client: TestClient):
    # Отметки нет вовсе — процесс ни разу не отчитался.
    response = api_client.get('/health/heartbeat/scheduler')
    assert response.status_code == 503
    assert response.json() == {'detail': 'scheduler: no heartbeat'}


def test_endpoint_stale(heartbeats_dir, api_client: TestClient):
    # Отметка есть, но старше порога: сдвигаем mtime в прошлое — настоящий
    # устаревший файл, без подмены времени.
    heartbeat.touch('scheduler', heartbeats_dir)
    stale_at = time.time() - HEARTBEAT_MAX_AGE_SECONDS[HeartbeatName.SCHEDULER] - 60
    os.utime(heartbeats_dir / 'scheduler', (stale_at, stale_at))

    response = api_client.get('/health/heartbeat/scheduler')
    assert response.status_code == 503
    assert response.json() == {'detail': 'scheduler: heartbeat stale'}


def test_endpoint_unknown_name(heartbeats_dir, api_client: TestClient):
    # Имя вне enum — 422 от валидации пути, а не 503.
    response = api_client.get('/health/heartbeat/unknown')
    assert response.status_code == 422


def test_dependency_returns_configured_dir():
    # Без override зависимость обязана отдавать каталог из настроек — иначе в
    # проде ручка смотрела бы не туда, а тесты этого не заметили бы.
    assert get_heartbeats_dir() == settings.HEARTBEATS_DIR


def test_every_name_has_max_age():
    # Новое имя в enum без порога уронило бы ручку по KeyError уже в проде.
    assert set(HEARTBEAT_MAX_AGE_SECONDS) == set(HeartbeatName)
