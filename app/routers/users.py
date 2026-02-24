from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    UploadFile,
)
from httpx import HTTPError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.status import HTTP_404_NOT_FOUND

from app.config import settings
from app.db import User
from app.dependencies import USERS_TAG, get_current_user, get_db
from app.firebase import delete_firebase_user
from app.helpers import (
    delete_user_image,
    get_annotated_users,
    get_user_deep_link,
    send_push_about_new_follower,
)
from app.logging import logger
from app.parsers import ItemInfoParseError, try_parse_item_by_link
from app.schemas import (
    AnnotatedOtherUserSchema,
    CurrentUserReadSchema,
    CurrentUserUpdateSchema,
    ItemInfoRequestSchema,
    ItemInfoResponseSchema,
    OtherUserSchema,
)

router = APIRouter(tags=[USERS_TAG])

# Константы
BASE_DIR = Path(__file__).parent.parent.parent
PROFILE_IMAGES_DIR = settings.MEDIA_ROOT / 'profile_images'


@router.get('/users/', response_model=list[AnnotatedOtherUserSchema])
def users(db: Session = Depends(get_db)):
    """Тестовый API, недоступен на проде."""
    if not settings.IS_DEBUG:
        raise HTTPException(status_code=404)
    user = db.execute(select(User).limit(1)).scalar_one()
    return get_annotated_users(db, user)


@router.get('/users/me', response_model=CurrentUserReadSchema)
def users_me(user: User = Depends(get_current_user)):
    return user


@router.put('/users/me')
def update_profile(
    update_data: CurrentUserUpdateSchema,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user.birth_date = update_data.birth_date
    user.display_name = update_data.display_name
    user.gender = update_data.gender
    db.add(user)
    db.commit()


@router.post('/set_profile_image')
def set_profile_image(
    request: Request,
    image: UploadFile,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    PROFILE_IMAGES_DIR.mkdir(exist_ok=True, parents=True)
    content = image.file.read()
    file_name = f'profile_image_user_{user.id}_{datetime.now().isoformat()}'
    file_path = PROFILE_IMAGES_DIR / file_name
    file_path.write_bytes(content)
    related_media_path = file_path.relative_to(settings.MEDIA_ROOT)
    user.photo_url = (
        f"{request.url.scheme}://{request.headers['host']}/media/{related_media_path}"
    )
    user.photo_path = str(file_path)
    db.add(user)
    db.commit()


@router.post('/delete_profile_image')
def delete_profile_image(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.photo_path:
        delete_user_image(user, db)


@router.get('/users/search', response_model=list[AnnotatedOtherUserSchema])
def search_users(
    q: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Поиск пользователей по имени. Возвращает первые 20 результатов."""
    q = q.strip()
    if not q:
        return []
    query = (
        select(User)
        .where(
            (User.id != current_user.id)
            & (
                User.display_name.icontains(q.capitalize())
                | User.display_name.icontains(q.lower())
            )
        )
        .limit(20)
    )
    found_users = db.execute(query).scalars().all()
    return get_annotated_users(db, current_user, found_users)


@router.get('/users/{user_id}', response_model=AnnotatedOtherUserSchema)
def get_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user = db.scalars(select(User).where(User.id == user_id)).one_or_none()
    if not user:
        raise HTTPException(HTTP_404_NOT_FOUND, 'User not found')
    return get_annotated_users(db, current_user, [user])[0]


@router.post('/delete_own_account')
def delete_own_account(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    logger.info('Удаление аккаунта: {user_id} {firebase_udi}')
    delete_firebase_user(user.firebase_uid)
    db.delete(user)
    db.commit()


@router.get('/users/{user_id}/followers', response_model=list[AnnotatedOtherUserSchema])
def user_followers(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user = db.scalars(select(User).where(User.id == user_id)).one()
    return get_annotated_users(db, current_user, user.followed_by)


@router.get('/users/{user_id}/follows', response_model=list[AnnotatedOtherUserSchema])
def users_followed_by_this_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user = db.scalars(select(User).where(User.id == user_id)).one()
    return get_annotated_users(db, current_user, user.follows)


@router.post('/follow/{follow_user_id}')
def follow_user(
    follow_user_id: UUID,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    follow_user = db.execute(select(User).where(User.id == follow_user_id)).scalar_one()
    if follow_user in user.follows:
        return
    user.follows.append(follow_user)
    db.commit()
    background_tasks.add_task(
        send_push_about_new_follower,
        target=follow_user,
        follower=user,
    )


@router.post('/unfollow/{unfollow_user_id}')
def unfollow_user(
    unfollow_user_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    unfollow_user = db.execute(
        select(User).where(User.id == unfollow_user_id)
    ).scalar_one()
    if unfollow_user not in user.follows:
        return
    user.follows.remove(unfollow_user)
    db.commit()


@router.get('/possible_friends')
def possible_friends(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AnnotatedOtherUserSchema]:
    if not user.vk_friends_data:
        return []
    vk_friend_ids = [str(vk_friend_data['id']) for vk_friend_data in user.vk_friends_data]
    query = (
        select(User)
        .where(User.vk_id.in_(vk_friend_ids))
        .where(~User.followed_by.any(User.id == user.id))
    )
    return get_annotated_users(db, user, query)


@router.post('/item_info_from_page')
async def get_item_info_from_page(
    request_data: ItemInfoRequestSchema,
    user: User = Depends(get_current_user),
) -> ItemInfoResponseSchema:
    try:
        try:
            result = await try_parse_item_by_link(
                str(request_data.link), request_data.html
            )
            logger.debug('result return value {result}', result=result)
        except ItemInfoParseError as ex:
            logger.warning(str(ex))
            result = None
            if request_data.html:
                logger.info(
                    f'Перезапрос html от сервера для превью: {request_data.link}'
                )
                try:
                    result = await try_parse_item_by_link(str(request_data.link))
                except ItemInfoParseError:
                    logger.warning(str(ex))
    except HTTPError as ex:
        logger.warning(repr(ex))
        result = None
    if result is None:
        raise HTTPException(detail='Ошибка получения данных', status_code=400)
    return result


@router.get('/invite_link/')
def get_invite_link(user: User = Depends(get_current_user)):
    return get_user_deep_link(user)
