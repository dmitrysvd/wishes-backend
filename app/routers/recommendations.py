from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_404_NOT_FOUND

from app.constants import RECOMMENDATION_CATEGORY_TITLES, RecommendationCategory
from app.db import User, Wish, WishRecommendation
from app.dependencies import WISHES_TAG, PaginationParams, get_current_user, get_db
from app.helpers.pagination import paginate
from app.helpers.recommendations import ordered_categories
from app.schemas import (
    RECOMMENDATION_EXAMPLE,
    PageSchema,
    RecommendationCategoryListSchema,
    RecommendationCategorySchema,
    RecommendationFullReadSchema,
    RecommendationSchema,
)

router = APIRouter(tags=[WISHES_TAG])

_AUTH_RESPONSE: dict[int | str, dict[str, Any]] = {
    HTTP_401_UNAUTHORIZED: {
        'description': 'Нет или истёк токен авторизации.',
        'content': {'application/json': {'example': {'detail': 'Not authenticated'}}},
    },
}

_CATEGORIES_DESCRIPTION = """\
Категории для экрана «не знаю, что хочу» (фича 0015). Первый запрос экрана:
клиент рисует список категорий как есть, тап по категории →
`GET /wish_recommendations?category=<code>`.

Отдаются только категории, в которых есть товары, — пустой категории на экране
не бывает. Порядок — порядок показа: бэк подстраивает его под юзера (пол,
возраст из профиля); без этих данных — дефолтный. Набор категорий у всех
одинаковый, таргетинг меняет только порядок.
"""

_LIST_DESCRIPTION = """\
Товары-рекомендации. С `category` — содержимое одной категории для экрана
«не знаю, что хочу»; без `category` — все товары подряд (старый плоский экран;
поведение сохранено для клиентов до 0015).

Порядок — от новых к старым, стабильный для offset/limit. Товар, который юзер
уже добавил из рекомендации, из списка не убирается: дубликаты хотелок
разрешены.

`category` — либо валидный код, либо параметр опущен; пустое значение
(`?category=`) — `422`, а не «без фильтра». `items: []` при `category` —
не штатно (категорию без товаров бэк не отдаёт; возможно только в момент
обновления контента): рисуй «пока нечего предложить» с возвратом к
категориям, ретрай не нужен.

Устаревший `id`: если между показом списка и сохранением контент обновили,
`POST /wishes` ответит `404 Recommendation not found` — клиент повторяет тот
же запрос без `recommendation_id` (форма не теряется; картинка тогда не
копируется).
"""


@router.get(
    '/wish_recommendations/categories',
    response_model=RecommendationCategoryListSchema,
    description=_CATEGORIES_DESCRIPTION,
    responses={
        200: {
            'description': (
                'Категории в порядке показа. `items: []` — контент не залит вовсе '
                '(единственное «пусто» на этом экране).'
            )
        },
        **_AUTH_RESPONSE,
    },
)
def list_recommendation_categories(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> RecommendationCategoryListSchema:
    return RecommendationCategoryListSchema(
        items=[
            RecommendationCategorySchema(
                code=category, title=RECOMMENDATION_CATEGORY_TITLES[category]
            )
            for category in ordered_categories(db, user)
        ]
    )


@router.get(
    '/wish_recommendations',
    response_model=PageSchema[RecommendationSchema],
    description=_LIST_DESCRIPTION,
    responses={
        200: {
            'description': (
                'Страница товаров; `items: []` — в категории (или вообще) пусто.'
            ),
            'content': {
                'application/json': {
                    'examples': {
                        'category': {
                            'summary': 'Категория hobby, одна страница',
                            'value': {
                                'items': [RECOMMENDATION_EXAMPLE],
                                'total': 1,
                                'has_next': False,
                                'has_previous': False,
                            },
                        },
                        'second_page': {
                            'summary': 'Вторая страница из трёх (limit=1, offset=1)',
                            'value': {
                                'items': [RECOMMENDATION_EXAMPLE],
                                'total': 3,
                                'has_next': True,
                                'has_previous': True,
                            },
                        },
                        'empty': {
                            'summary': 'Пусто',
                            'value': {
                                'items': [],
                                'total': 0,
                                'has_next': False,
                                'has_previous': False,
                            },
                        },
                    }
                }
            },
        },
        **_AUTH_RESPONSE,
        422: {
            'description': (
                '`category` не из списка кодов (в т.ч. пустое `?category=`), либо '
                '`limit`/`offset` вне допустимого. Форма — стандартная '
                '`HTTPValidationError`.'
            ),
            'content': {
                'application/json': {
                    'example': {
                        'detail': [
                            {
                                'type': 'enum',
                                'loc': ['query', 'category'],
                                'msg': (
                                    "Input should be 'beauty', 'jewelry', 'gadgets', "
                                    "'home', 'books', 'hobby', 'clothes' or 'kids'"
                                ),
                                'input': 'cars',
                            }
                        ]
                    }
                }
            },
        },
    },
)
def list_recommendations(
    pagination: Annotated[PaginationParams, Depends()],
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    category: Annotated[
        RecommendationCategory | None,
        Query(
            description=(
                'Код категории из `GET /wish_recommendations/categories`. '
                'Опущен — все товары без фильтра.'
            )
        ),
    ] = None,
):
    # Стабильный порядок обязателен для корректной offset/limit-пагинации.
    query = select(WishRecommendation).order_by(
        WishRecommendation.created_at.desc(), WishRecommendation.id.desc()
    )
    if category is not None:
        query = query.where(WishRecommendation.category == category)
    return paginate(db, query, pagination, RecommendationSchema)


@router.get(
    '/wish_recommendations/{rec_id}',
    response_model=RecommendationFullReadSchema,
    description=(
        'Одна рекомендация со счётчиком добавлений. Экранам клиента не нужна: '
        'форму создания заполняй из элемента списка, свежую цену бэк возьмёт '
        'сам при `POST /wishes`.'
    ),
    responses={
        200: {
            'description': (
                'Рекомендация; те же поля, что в списке, плюс `wishes_count`.'
            )
        },
        **_AUTH_RESPONSE,
        HTTP_404_NOT_FOUND: {
            'description': (
                'Рекомендации с таким `id` нет (в т.ч. удалена при обновлении '
                'контента).'
            ),
            'content': {
                'application/json': {'example': {'detail': 'Recommendation not found'}}
            },
        },
    },
)
def get_recommendation(
    rec_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rec = db.scalars(
        select(WishRecommendation).where(WishRecommendation.id == rec_id)
    ).one_or_none()
    if not rec:
        raise HTTPException(HTTP_404_NOT_FOUND, 'Recommendation not found')
    rec.wishes_count = db.scalar(  # ty: ignore[invalid-assignment]
        select(func.count(Wish.id)).where(Wish.recommendation_id == rec_id)
    )
    return rec
