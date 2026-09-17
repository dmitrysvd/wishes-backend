"""Загрузчик контента рекомендаций (фича 0015): картинки на диск, upsert,
`--replace` сносит лишнее и отвязывает хотелки."""

import json
import os
import runpy
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import RecommendationCategory
from app.db import User, Wish, WishRecommendation
from app.utils import utc_now
from scripts import load_recommendations
from scripts.load_recommendations import main

LINK = 'https://www.wildberries.ru/catalog/1/detail.aspx'


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok_image(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b'img', headers={'content-type': 'image/webp'})


@pytest.fixture
def images_dir(tmp_path: Path, mocker) -> Path:
    mocker.patch('scripts.load_recommendations.IMAGES_DIR', tmp_path)
    return tmp_path


def _content(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / 'content.json'
    path.write_text(json.dumps(records), encoding='utf-8')
    return path


def test_loads_with_image(db: Session, tmp_path: Path, images_dir: Path):
    content = _content(
        tmp_path,
        [
            {
                'category': 'hobby',
                'link': LINK,
                'title': 'Alias',
                'description': '',
                'price': '739',
                'image_url': 'https://cdn/1.webp',
            }
        ],
    )
    main([str(content)], client=_client(_ok_image))
    rec = db.scalars(select(WishRecommendation)).one()
    assert rec.id == uuid5(NAMESPACE_URL, LINK)
    assert rec.category == RecommendationCategory.hobby
    assert rec.description is None
    assert rec.image_url is not None and rec.image_url.startswith(
        '/media/recommendation_images/'
    )
    assert (images_dir / rec.image_url.rsplit('/', 1)[1]).read_bytes() == b'img'


def test_image_download_failure_keeps_row(db: Session, tmp_path: Path, images_dir):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    content = _content(
        tmp_path,
        [{'category': 'kids', 'link': LINK, 'title': 'T', 'image_url': 'https://c/x'}],
    )
    main([str(content)], client=_client(handler))
    rec = db.scalars(select(WishRecommendation)).one()
    assert rec.image_url is None


def test_rerun_updates_and_replace_prunes(db: Session, tmp_path: Path, images_dir):
    old = WishRecommendation(
        title='Old', link='https://old', category=RecommendationCategory.books
    )
    user = User(display_name='U', firebase_uid='u1', registered_at=utc_now())
    db.add_all([old, user])
    db.commit()
    wish = Wish(user_id=user.id, name='w', recommendation_id=old.id)
    db.add(wish)
    db.commit()

    content = _content(
        tmp_path, [{'category': 'hobby', 'link': LINK, 'title': 'V1', 'price': 1}]
    )
    main([str(content)], client=_client(_ok_image))
    content = _content(
        tmp_path, [{'category': 'gadgets', 'link': LINK, 'title': 'V2', 'price': 2}]
    )
    main([str(content), '--replace'], client=_client(_ok_image))

    rows = db.scalars(select(WishRecommendation)).all()
    assert [(r.title, r.category, r.price) for r in rows] == [
        ('V2', RecommendationCategory.gadgets, 2)
    ]
    db.refresh(wish)
    assert wish.recommendation_id is None


def test_script_main_execution(db: Session, tmp_path: Path, images_dir, mocker):
    # Пустой вход: покрываем ветку `__main__` без сети.
    content = _content(tmp_path, [])
    mocker.patch('sys.argv', ['load_recommendations', str(content)])
    runpy.run_path(os.path.abspath(load_recommendations.__file__), run_name='__main__')
    assert db.scalars(select(WishRecommendation)).all() == []
