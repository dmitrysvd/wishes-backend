from decimal import Decimal
from hashlib import md5
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.status import HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND

from app.config import settings
from app.db import User, Wish
from app.dependencies import WISHES_TAG, get_current_user, get_current_user_wish, get_db
from app.schemas import WishReadSchema, WishWriteSchema

router = APIRouter(tags=[WISHES_TAG])

# Константы
BASE_DIR = Path(__file__).parent.parent.parent
WISH_IMAGES_DIR = settings.MEDIA_ROOT / 'wish_images'


@router.post('/wishes', response_model=WishReadSchema)
def add_wish(
    wish_data: WishWriteSchema,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    wish = Wish(
        user_id=user.id,
        name=wish_data.name,
        description=wish_data.description,
        price=wish_data.price,
        link=str(wish_data.link) if wish_data.link else None,
    )
    db.add(wish)
    db.commit()
    return wish


@router.get('/wishes', response_model=list[WishReadSchema])
def my_wishes(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = Wish.get_active_wish_query().where(Wish.user == user)
    return db.scalars(query)


@router.get('/reserved_wishes', response_model=list[WishReadSchema])
def my_reserved_wishes(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    query = Wish.get_active_wish_query().where(Wish.reserved_by == user)
    return db.scalars(query)


@router.get('/wishes/{wish_id}', response_model=WishReadSchema)
def get_wish(
    wish_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    wish = db.scalars(select(Wish).where(Wish.id == wish_id)).one_or_none()
    if not wish or (wish.is_archived and wish.user != user):
        raise HTTPException(HTTP_404_NOT_FOUND, 'Wish not found')
    return wish


@router.put('/wishes/{wish_id}')
def update_wish(
    wish_data: WishWriteSchema,
    db: Session = Depends(get_db),
    wish: Wish = Depends(get_current_user_wish),
):
    wish.name = wish_data.name
    wish.description = wish_data.description
    wish.price = Decimal(wish_data.price) if wish_data.price else None
    wish.link = str(wish_data.link) if wish_data.link else None
    db.add(wish)
    db.commit()


@router.delete('/wishes/{wish_id}')
def delete_wish(
    db: Session = Depends(get_db),
    wish: Wish = Depends(get_current_user_wish),
):
    db.delete(wish)
    db.commit()


@router.post('/wishes/{wish_id}/image')
def upload_wish_image(
    file: UploadFile,
    wish: Wish = Depends(get_current_user_wish),
    db: Session = Depends(get_db),
):
    WISH_IMAGES_DIR.mkdir(exist_ok=True, parents=True)
    content = file.file.read()
    content_hash = md5(content).hexdigest()
    file_name = f'{content_hash}'
    file_path = WISH_IMAGES_DIR / file_name
    file_path.write_bytes(content)
    wish.image = file_name
    db.add(wish)
    db.commit()


@router.delete('/wishes/{wish_id}/image')
def delete_wish_image(
    wish: Wish = Depends(get_current_user_wish),
    db: Session = Depends(get_db),
):
    wish.image = None
    db.add(wish)
    db.commit()


@router.get('/users/{user_id}/wishes', response_model=list[WishReadSchema])
def user_wishes(user_id: UUID, db: Session = Depends(get_db)):
    user = db.scalars(select(User).where(User.id == user_id)).one_or_none()
    if not user:
        raise HTTPException(404, 'Пользователь не найден')
    query = Wish.get_active_wish_query().where(Wish.user == user)
    return db.scalars(query)


@router.post('/wishes/{wish_id}/reserve', response_class=Response)
def reserve_wish(
    wish_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = Wish.get_active_wish_query().where(Wish.id == wish_id)
    wish = db.scalars(query).one_or_none()
    if not wish:
        raise HTTPException(HTTP_404_NOT_FOUND, 'Wish not found')
    if wish.user == current_user:
        raise HTTPException(HTTP_403_FORBIDDEN, 'Cannot reserve own wish')
    if wish.reserved_by and wish.reserved_by != current_user:
        raise HTTPException(HTTP_403_FORBIDDEN, 'Reserved by someone else')
    wish.reserved_by = current_user
    db.add(wish)
    db.commit()


@router.post('/wishes/{wish_id}/cancel_reservation', response_class=Response)
def cancel_wish_reservation(
    wish_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    wish = db.execute(select(Wish).where(Wish.id == wish_id)).scalar_one_or_none()
    if not wish:
        raise HTTPException(404, 'Wish not found')
    if wish.reserved_by and wish.reserved_by != current_user:
        raise HTTPException(HTTP_403_FORBIDDEN, 'Reserved by someone else')
    wish.reserved_by = None
    db.add(wish)
    db.commit()


@router.post('/wishes/{wish_id}/archive', response_class=Response)
def archive_wish(
    db: Session = Depends(get_db), wish: Wish = Depends(get_current_user_wish)
):
    wish.is_archived = True
    db.add(wish)
    db.commit()


@router.post('/wishes/{wish_id}/unarchive', response_class=Response)
def unarchive_wish(
    db: Session = Depends(get_db), wish: Wish = Depends(get_current_user_wish)
):
    wish.is_archived = False
    db.add(wish)
    db.commit()


@router.get('/archived_wishes', response_model=list[WishReadSchema])
def archived_wishes(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return db.scalars(select(Wish).where(Wish.user == user, Wish.is_archived == True))
