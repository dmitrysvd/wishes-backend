import os
import runpy
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session

from app.config import settings
from app.cron_scripts import backfill_profile_images
from app.cron_scripts.backfill_profile_images import main
from app.db import User
from app.utils import utc_now


def _client(handler) -> httpx.Client:
    # Реальный httpx-клиент на MockTransport: тестируем логику скачивания без моков.
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def mocked_profile_media(tmp_path: Path, mocker) -> Path:
    # Запись на диск живёт в user_helpers.save_profile_image_bytes.
    mocker.patch('app.helpers.user_helpers.PROFILE_IMAGES_DIR', tmp_path)
    mocker.patch('app.helpers.user_helpers.settings.MEDIA_ROOT', tmp_path.parent)
    return tmp_path


def _make_user(db: Session, uid: str, **kwargs) -> User:
    user = User(
        display_name='U',
        firebase_uid=uid,
        registered_at=utc_now(),
        **kwargs,
    )
    db.add(user)
    db.commit()
    return user


def test_main_migrates_external_photo(db: Session, mocked_profile_media: Path):
    user = _make_user(db, 'uid_ext', photo_url='https://sun1-1.userapi.com/abc.jpg')

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'img-bytes')

    main(client=_client(handler))

    db.refresh(user)
    # Фото легло на диск, наружу отдаётся только наш хост.
    assert user.photo_path is not None
    assert Path(user.photo_path).read_bytes() == b'img-bytes'
    assert user.photo_url is not None
    assert user.photo_url.startswith(f'{settings.FRONTEND_URL}/media/')
    # Мигрированное фото — не кастомное, refresh-на-логине сможет его обновлять.
    assert user.photo_is_custom is False


def test_main_clears_broken_link(db: Session, mocked_profile_media: Path):
    user = _make_user(db, 'uid_dead', photo_url='https://sun1-9.userapi.com/dead.jpg')

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    main(client=_client(handler))

    db.refresh(user)
    # Протухшую ссылку наружу не отдаём — фронт падает на инициалы.
    assert user.photo_url is None
    assert user.photo_path is None


def test_main_skips_custom_and_on_disk(db: Session, mocked_profile_media: Path):
    # Уже на диске (кастомное фото пользователя) — не трогаем.
    on_disk = _make_user(
        db,
        'uid_custom',
        photo_url=f'{settings.FRONTEND_URL}/media/profile_images/mine',
        photo_path='/data/media/profile_images/mine',
        photo_is_custom=True,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError('кастомное/дисковое фото не должно скачиваться')

    main(client=_client(handler))

    db.refresh(on_disk)
    assert on_disk.photo_path == '/data/media/profile_images/mine'
    assert on_disk.photo_is_custom is True


def test_dry_run_changes_nothing(db: Session, mocked_profile_media: Path):
    good = _make_user(db, 'dry_good', photo_url='https://cdn.test/good.jpg')
    dead = _make_user(db, 'dry_dead', photo_url='https://cdn.test/dead.jpg')

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/good.jpg':
            return httpx.Response(200, content=b'x')
        return httpx.Response(404)

    main(client=_client(handler), dry_run=True)

    db.refresh(good)
    db.refresh(dead)
    # dry-run ничего не пишет: живую не переносит, битую не обнуляет.
    assert good.photo_path is None
    assert good.photo_url == 'https://cdn.test/good.jpg'
    assert dead.photo_url == 'https://cdn.test/dead.jpg'


def test_main_creates_own_client_when_not_passed(db: Session):
    # Нет подходящих юзеров → клиент создаётся и закрывается, ничего не качаем.
    main()


def test_script_main_execution(db: Session, mocker):
    # Фикстура db патчит app.db.SessionLocal → свежий __main__-модуль,
    # импортируя SessionLocal, подхватит тестовую сессию. Юзеров нет —
    # main() отработает вхолостую, покрывая ветку `if __name__ == '__main__'`.
    # argv подменяем, иначе argparse распарсит аргументы pytest.
    mocker.patch('sys.argv', ['backfill_profile_images'])
    script_path = os.path.abspath(backfill_profile_images.__file__)
    runpy.run_path(script_path, run_name='__main__')
