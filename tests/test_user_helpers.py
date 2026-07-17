from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session

from app.config import settings
from app.db import User
from app.helpers.user_helpers import (
    download_avatar_bytes,
    guess_image_extension,
    refresh_avatar_on_login,
    upscale_google_avatar_url,
)
from app.utils import utc_now

PNG = b'\x89PNG\r\n\x1a\n' + b'rest'
JPEG = b'\xff\xd8\xff' + b'rest'


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def mocked_profile_media(tmp_path: Path, mocker) -> Path:
    mocker.patch('app.helpers.user_helpers.PROFILE_IMAGES_DIR', tmp_path)
    mocker.patch('app.helpers.user_helpers.settings.MEDIA_ROOT', tmp_path.parent)
    return tmp_path


def _make_user(db: Session, uid: str, **kwargs) -> User:
    user = User(display_name='U', firebase_uid=uid, registered_at=utc_now(), **kwargs)
    db.add(user)
    db.commit()
    return user


# --- upscale_google_avatar_url ---


def test_upscale_google_replaces_size_token():
    url = 'https://lh3.googleusercontent.com/a/ABC=s96-c'
    assert upscale_google_avatar_url(url) == (
        'https://lh3.googleusercontent.com/a/ABC=s512-c'
    )


def test_upscale_google_appends_when_no_token():
    url = 'https://lh3.googleusercontent.com/a/ABC'
    assert upscale_google_avatar_url(url) == (
        'https://lh3.googleusercontent.com/a/ABC=s512-c'
    )


def test_upscale_non_google_unchanged():
    url = 'https://sun1-1.userapi.com/abc.jpg?size=200x200'
    assert upscale_google_avatar_url(url) == url


# --- guess_image_extension ---


@pytest.mark.parametrize(
    ('content', 'ext'),
    [
        (PNG, '.png'),
        (JPEG, '.jpg'),
        (b'GIF89a...', '.gif'),
        (b'RIFF\x00\x00\x00\x00WEBPVP8 ', '.webp'),
        (b'not-an-image', ''),
    ],
)
def test_guess_image_extension(content: bytes, ext: str):
    assert guess_image_extension(content) == ext


# --- download_avatar_bytes ---


def test_download_upscales_google_and_returns_bytes():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen['url'] = str(request.url)
        return httpx.Response(200, content=PNG)

    result = download_avatar_bytes(
        'https://lh3.googleusercontent.com/a/ABC=s96-c', _client(handler)
    )
    assert result == PNG
    # Скачиваем именно апскейленный URL.
    assert seen['url'].endswith('=s512-c')


def test_download_returns_none_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    assert download_avatar_bytes('https://cdn.test/x.jpg', _client(handler)) is None


def test_download_creates_own_client_when_not_passed(mocker):
    # Ветка client=None: подсовываем клиент на MockTransport через фабрику.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=JPEG)

    mocker.patch('app.helpers.user_helpers.httpx.Client', return_value=_client(handler))
    assert download_avatar_bytes('https://cdn.test/x.jpg') == JPEG


# --- refresh_avatar_on_login ---


def test_refresh_skips_custom_photo(db: Session):
    user = _make_user(
        db, 'u_custom', photo_url='mine', photo_path='/p', photo_is_custom=True
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError('кастомное фото не должно скачиваться')

    refresh_avatar_on_login(user, 'https://cdn.test/x.jpg', db, _client(handler))
    db.refresh(user)
    assert user.photo_url == 'mine'
    assert user.photo_is_custom is True


def test_refresh_skips_when_no_social_url(db: Session):
    user = _make_user(db, 'u_nourl')

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError('без соц-URL скачивать нечего')

    refresh_avatar_on_login(user, None, db, _client(handler))
    db.refresh(user)
    assert user.photo_url is None


def test_refresh_downloads_and_saves(db: Session, mocked_profile_media: Path):
    user = _make_user(db, 'u_ok')

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PNG)

    refresh_avatar_on_login(user, 'https://cdn.test/x.png', db, _client(handler))
    db.refresh(user)
    assert user.photo_path is not None
    assert user.photo_path.endswith('.png')
    assert user.photo_url is not None
    assert user.photo_url.startswith(f'{settings.FRONTEND_URL}/media/')
    assert user.photo_is_custom is False


def test_refresh_keeps_existing_on_download_failure(db: Session):
    user = _make_user(db, 'u_fail', photo_url='https://hotelki.pro/media/old')

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    refresh_avatar_on_login(user, 'https://cdn.test/x.jpg', db, _client(handler))
    db.refresh(user)
    # Скачать не вышло — текущее фото не трогаем.
    assert user.photo_url == 'https://hotelki.pro/media/old'
