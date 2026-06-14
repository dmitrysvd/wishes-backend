"""Заливает стартовые рекомендации напрямую в БД (вместо Alembic-миграции).

Запуск:

    DATABASE_URL=<целевая база> uv run python scripts/load_recommendations.py

Читает scripts/recommendations.json и upsert'ит записи в wish_recommendation.
UUID детерминирован (uuid5 от link), вставка идемпотентна (on_conflict_do_nothing),
поэтому скрипт можно гонять повторно. Описания не переносим — это маркетинговый
мусор источника, в БД кладём NULL. Сегментация target_gender пока остаётся только
в JSON и в схему не пишется.

ВАЖНО: картинки (поле image_url ссылается на /media/recommendation_images/...)
нужно отдельно положить на media-том целевого сервера, например:

    rsync -a media/recommendation_images/ <prod>:$MEDIA_ROOT/recommendation_images/
"""

import json
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.db import WishRecommendation

JSON_PATH = Path(__file__).parent / 'recommendations.json'


def build_rows() -> list[dict]:
    records = json.loads(JSON_PATH.read_text(encoding='utf-8'))
    rows: list[dict] = []
    for rec in records:
        rows.append(
            {
                'id': uuid5(NAMESPACE_URL, rec['link']),
                'title': rec['title'][:250],
                'description': None,
                'price': rec['price'],
                'link': rec['link'][:500],
                'image_url': rec['image_url'],
            }
        )
    return rows


def main() -> None:
    rows = build_rows()
    engine = create_engine(settings.DATABASE_URL)
    stmt = insert(WishRecommendation).on_conflict_do_nothing(index_elements=['id'])
    with engine.begin() as conn:
        result = conn.execute(stmt, rows)
    print(f'подготовлено {len(rows)}, вставлено {result.rowcount}', file=sys.stderr)


if __name__ == '__main__':
    main()
