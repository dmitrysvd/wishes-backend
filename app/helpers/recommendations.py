"""Рекомендации по категориям (фича 0015): порядок категорий под юзера и
копирование картинки рекомендации в хотелку."""

import shutil
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import (
    RECOMMENDATION_CATEGORY_TITLES,
    Gender,
    RecommendationCategory,
)
from app.db import User, WishRecommendation

# Картинки рекомендаций лежат на media-томе под этим префиксом; `image_url`
# отдаётся относительным путём, как `WishReadSchema.image`, — старый клиент
# дописывает origin API сам.
RECOMMENDATION_IMAGES_PREFIX = '/media/recommendation_images/'

# Таргетинг = только порядок (intent 0015): категории из списка идут первыми в
# этом порядке, остальные — в дефолтном (порядок `RECOMMENDATION_CATEGORY_TITLES`).
_GENDER_FIRST: dict[Gender, list[RecommendationCategory]] = {
    Gender.female: [
        RecommendationCategory.beauty,
        RecommendationCategory.jewelry,
        RecommendationCategory.clothes,
    ],
    Gender.male: [
        RecommendationCategory.gadgets,
        RecommendationCategory.hobby,
        RecommendationCategory.home,
    ],
}


def ordered_categories(db: Session, user: User) -> list[RecommendationCategory]:
    """Категории, в которых есть товары, в порядке показа для `user`."""
    present = set(db.scalars(select(WishRecommendation.category).distinct()).all())
    first = _GENDER_FIRST.get(user.gender) if user.gender else None
    order = [*(first or []), *RECOMMENDATION_CATEGORY_TITLES]
    seen: set[RecommendationCategory] = set()
    result: list[RecommendationCategory] = []
    for category in order:
        if category in present and category not in seen:
            seen.add(category)
            result.append(category)
    return result


def copy_recommendation_image(
    image_url: str | None, media_root: Path, wish_images_dir: Path
) -> str | None:
    """Скопировать картинку рекомендации в каталог картинок хотелок; вернуть имя
    файла для `Wish.image`. Нет картинки / чужой URL / файла нет на диске →
    None: хотелка создаётся без картинки, сохранение не падает."""
    if not image_url or not image_url.startswith(RECOMMENDATION_IMAGES_PREFIX):
        return None
    file_name = image_url.removeprefix(RECOMMENDATION_IMAGES_PREFIX)
    source = media_root / 'recommendation_images' / file_name
    if not source.is_file():
        logger.warning('Картинка рекомендации отсутствует на диске: {}', source)
        return None
    wish_images_dir.mkdir(exist_ok=True, parents=True)
    shutil.copyfile(source, wish_images_dir / file_name)
    return file_name
