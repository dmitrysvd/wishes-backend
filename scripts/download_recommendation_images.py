"""Скачивает картинки рекомендаций с CDN-источника на наш media-том.

Запуск (нужен прямой RU-egress; в namespace Claude — через netns хоста):

    sudo nsenter -t 1 -n env -u http_proxy -u https_proxy -u HTTP_PROXY \
        -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
        uv run python scripts/download_recommendation_images.py

Берёт scripts/recommendations.json, для каждой записи качает картинку,
кладёт её в MEDIA_ROOT/recommendation_images/<md5>.<ext> (имя по содержимому,
как у картинок вишей) и переписывает image_url на относительный путь
/media/recommendation_images/<md5>.<ext>. Идемпотентно: записи, у которых
image_url уже указывает на наш /media, пропускаются.
"""

import json
import mimetypes
import sys
from hashlib import md5
from pathlib import Path

import httpx

from app.config import settings

JSON_PATH = Path(__file__).parent / 'recommendations.json'
IMAGES_DIR = settings.MEDIA_ROOT / 'recommendation_images'
MEDIA_PREFIX = '/media/recommendation_images'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/124.0 Safari/537.36'
    ),
}


def ext_for(content_type: str | None, url: str) -> str:
    # Расширение нужно nginx, чтобы отдать корректный mime. Берём из заголовка,
    # с откатом на расширение из URL и на .jpg.
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(';')[0].strip())
        if guessed:
            return '.jpg' if guessed == '.jpe' else guessed
    suffix = Path(url.split('?')[0]).suffix.lower()
    return suffix if suffix in {'.jpg', '.jpeg', '.png', '.webp'} else '.jpg'


def main() -> None:
    records = json.loads(JSON_PATH.read_text(encoding='utf-8'))
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        for rec in records:
            url = rec.get('image_url')
            if not url or url.startswith(MEDIA_PREFIX):
                continue  # уже локальная картинка — пропускаем
            resp = client.get(url)
            if resp.status_code != 200 or not resp.content:
                print(f'[{url}] HTTP {resp.status_code}, пропуск', file=sys.stderr)
                rec['image_url'] = None
                continue
            content = resp.content
            file_name = md5(content).hexdigest() + ext_for(
                resp.headers.get('content-type'), url
            )
            (IMAGES_DIR / file_name).write_bytes(content)
            rec['image_url'] = f'{MEDIA_PREFIX}/{file_name}'
            downloaded += 1
            print(f'[ok] {file_name} <- {url}', file=sys.stderr)

    JSON_PATH.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )
    print(f'скачано {downloaded}, всего записей {len(records)}', file=sys.stderr)


if __name__ == '__main__':
    main()
