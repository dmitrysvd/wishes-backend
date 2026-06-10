from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.status import HTTP_404_NOT_FOUND

from app.db import WishRecommendation
from app.dependencies import WISHES_TAG, get_db
from app.schemas import RecommendationFullReadSchema, RecommendationSchema

router = APIRouter(tags=[WISHES_TAG])


@router.get('/wish_recommendations', response_model=list[RecommendationSchema])
def list_recommendations(db: Session = Depends(get_db)):
    query = select(WishRecommendation)
    return db.scalars(query)


@router.get(
    '/wish_recommendations/{rec_id}', response_model=RecommendationFullReadSchema
)
def get_recommendation(rec_id: UUID, db: Session = Depends(get_db)):
    from app.db import Wish

    rec = db.scalars(
        select(WishRecommendation).where(WishRecommendation.id == rec_id)
    ).one_or_none()
    if not rec:
        raise HTTPException(HTTP_404_NOT_FOUND, 'Recommendation not found')
    rec.wishes_count = db.scalar(  # ty: ignore[invalid-assignment]
        select(func.count(Wish.id)).where(Wish.recommendation_id == rec_id)
    )
    return rec
