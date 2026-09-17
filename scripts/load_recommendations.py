"""Загружает контент рекомендаций (фича 0015) в БД и картинки на media-том.

Запуск (в контейнере app на проде, где есть DATABASE_URL и MEDIA_ROOT):

    uv run python scripts/load_recommendations.py /path/to/content.json [--replace]

Вход — JSON-массив объектов `{category, link, title, description, price,
image_url}` (см. wishes-product/features/0015-recommendation-categories/
content.json; `image_url` там — внешний URL магазина). Для каждой записи
картинка скачивается в MEDIA_ROOT/recommendation_images/<md5>.<ext> и в БД
кладётся относительный путь /media/recommendation_images/<md5>.<ext> — ровно
то, что клиент ждёт в `RecommendationSchema.image_url`. Не скачалась —
запись грузится без картинки.

Идемпотентно: id = uuid5(link), повтор обновляет title/price/category/картинку.
`--replace` перед загрузкой сносит рекомендации, которых нет во входе
(хотелки, ссылающиеся на них, теряют `recommendation_id`).
"""

import argparse
import json
import mimetypes
import sys
from hashlib import md5
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx
from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import RecommendationCategory
from app.db import SessionLocal, Wish, WishRecommendation
from app.helpers.recommendations import RECOMMENDATION_IMAGES_PREFIX

IMAGES_DIR = settings.MEDIA_ROOT / 'recommendation_images'


def download_image(url: str, client: httpx.Client, images_dir: Path) -> str | None:
    """Скачать картинку в `images_dir`; вернуть её путь для `image_url`."""
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f'картинка не скачалась {url}: {exc}', file=sys.stderr)
        return None
    content_type = response.headers.get('content-type', '').split(';')[0]
    extension = mimetypes.guess_extension(content_type) or '.jpg'
    file_name = f'{md5(response.content).hexdigest()}{extension}'
    images_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / file_name).write_bytes(response.content)
    return f'{RECOMMENDATION_IMAGES_PREFIX}{file_name}'


def build_rows(
    records: list[dict], client: httpx.Client, images_dir: Path
) -> list[dict]:
    rows: list[dict] = []
    for rec in records:
        image_url = rec.get('image_url')
        rows.append(
            {
                'id': uuid5(NAMESPACE_URL, rec['link']),
                'title': rec['title'][:250],
                'description': (rec.get('description') or None),
                'price': rec.get('price'),
                'link': rec['link'][:500],
                'image_url': (
                    download_image(image_url, client, images_dir) if image_url else None
                ),
                'category': RecommendationCategory(rec['category']),
            }
        )
    return rows


def load(db: Session, rows: list[dict], replace: bool) -> None:
    if replace:
        keep = [row['id'] for row in rows]
        db.execute(
            update(Wish)
            .where(Wish.recommendation_id.is_not(None))
            .where(Wish.recommendation_id.not_in(keep))
            .values(recommendation_id=None)
        )
        db.execute(delete(WishRecommendation).where(WishRecommendation.id.not_in(keep)))
    if not rows:
        db.commit()
        return
    stmt = insert(WishRecommendation).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=['id'],
        set_={
            column: getattr(stmt.excluded, column)
            for column in ('title', 'description', 'price', 'image_url', 'category')
        },
    )
    db.execute(stmt)
    db.commit()


def main(argv: list[str] | None = None, client: httpx.Client | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('content', type=Path)
    parser.add_argument('--replace', action='store_true')
    args = parser.parse_args(argv)
    records = json.loads(args.content.read_text(encoding='utf-8'))
    with client or httpx.Client(timeout=20, follow_redirects=True) as http:
        rows = build_rows(records, http, IMAGES_DIR)
    with SessionLocal() as db:
        load(db, rows, args.replace)
    print(f'записано {len(rows)}', file=sys.stderr)


if __name__ == '__main__':
    main()
