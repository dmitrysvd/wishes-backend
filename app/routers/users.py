from uuid import UUID

import httpx
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    UploadFile,
)
from httpx import HTTPError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.status import HTTP_404_NOT_FOUND

from app.config import settings
from app.constants import FollowAction, FollowEventSource
from app.db import FollowEvent, User
from app.dependencies import USERS_TAG, get_current_user, get_db, get_store_client
from app.firebase import delete_firebase_user
from app.helpers import (
    IMAGE_UPLOAD_RESPONSES,
    delete_user_image,
    get_annotated_users,
    get_user_deep_link,
    read_uploaded_image,
    save_profile_image_bytes,
)
from app.helpers.store_price import build_item_info
from app.logging import logger
from app.parsers import ItemInfoParseError, try_parse_item_by_link
from app.schemas import (
    AnnotatedOtherUserSchema,
    CurrentUserReadSchema,
    CurrentUserUpdateSchema,
    FollowActionSchema,
    ItemInfoRequestSchema,
    ItemInfoResponseSchema,
)

router = APIRouter(tags=[USERS_TAG])

# Пуш «новый подписчик» — побочный эффект `POST /follow` (фича 0024 добавила
# `delivery_id`, маркер `via=push` и ссылку на свой список подписчиков).
# Структурно, для аудитора и кодгена фронта (PROTOCOL.md §7).
_NEW_FOLLOWER_PUSH_PAYLOAD = {
    'kind': 'new_follower',
    'notification': {
        'title': 'У вас новый подписчик',
        'body': 'На вас подписался Иван Петров',
    },
    'data': {
        'click_action': 'FLUTTER_NOTIFICATION_CLICK',
        'type': 'new_follower',
        'delivery_id': '5c1c9a2e-7b1d-4e3a-9f0a-2d6b8c4e1a77',
        'link': (
            'https://hotelki.pro/user'
            '?userId=9b2d5e4a-1c3f-4a2b-8d6e-0f1a2b3c4d5e&via=push#'
        ),
        'title': 'У вас новый подписчик',
        'body': 'На вас подписался Иван Петров',
    },
    'fields': {
        'type': 'Вид пуша, `new_follower`; клиенту для роутинга не нужен.',
        'link': (
            'Открывать через роутер, как остальные пуши. Один подписчик → его '
            'профиль (S5): `{FRONTEND_URL}/user?userId=<uuid>&via=push#`; `via=push` '
            '— маркер «открыт из пуша»: CTA-блока нет, подписка с профиля идёт с '
            '`source=push`, кнопка по правилу «Подписаться в ответ» (см. '
            '`x-workflow` у `GET /users/{user_id}`). Несколько подписчиков → свой '
            'список подписчиков (`followers_page`): '
            '`{FRONTEND_URL}/followers?userId=<свой id>&followedBy=true#`.'
        ),
        'delivery_id': (
            'UUID строки лога отправки — тело `POST /push/opened` при открытии.'
        ),
    },
    'texts': {
        'one': {
            'title': 'У вас новый подписчик',
            'body': 'На вас подписался {display_name}',
        },
        'many': {
            'title': 'У вас новый подписчик',
            'body': 'На вас подписались {display_name первого} и ещё {N - 1}',
        },
        'rules': (
            'Один пуш за прогон на все новые подписки получателя. Подписка, '
            'оформленная автоматически при регистрации по инвайт-ссылке, в этот пуш '
            'не входит — о ней пригласившему приходит `invite_joined` '
            '(`x-push-payload` у `POST /auth/firebase`, `POST /auth/vk/vkid`).'
        ),
    },
}

# Правила экрана профиля (S5) для фичи 0024: CTA-блок подписки при входе по
# ссылке шеринга и кнопка «Подписаться в ответ».
_PROFILE_WORKFLOW = [
    'Маркер пути входа — параметры URL `/user?userId=…`: `ref` — ссылка шеринга '
    '(`GET /invite_link/`); `via=push` — ссылка из пуша; ни того, ни другого — '
    'прочее (переход внутри приложения, F5 на вебе).',
    'CTA-блок над списком показывается, только если одновременно: URL несёт `ref`, '
    'юзер авторизован, `userId` — не свой id, в ответе `followed_by_me == false`. '
    'Иначе блока нет (в т.ч. из поиска, списков и пушей).',
    'Текст CTA: `follows_me == true` → «{display_name} уже подписан(а) на вас — '
    'подписаться в ответ»; иначе «Подписаться на {display_name} — напомним о дне '
    'рождения и покажем новые хотелки». Одна кнопка.',
    'Тап CTA → `POST /follow/{userId}` с `source=deeplink`. `200` → блок скрыть, '
    'кнопка профиля — «вы подписаны». Не-2xx/сеть → тост ошибки, блок и кнопка в '
    'исходном состоянии, можно повторить (повтор идемпотентен).',
    'Кнопка подписки на любом чужом профиле: `follows_me == true && followed_by_me '
    '== false` → подпись «Подписаться в ответ», иначе как сейчас. Действие то же — '
    '`POST /follow/{userId}`; `source` — по пути входа (см. `FollowActionSchema`).',
    'Состояние блока и кнопки — из ответа этой операции при каждом открытии; '
    'открытие той же ссылки повторно (F5) снова покажет CTA, если не подписан.',
    'Гость (без авторизации) эту операцию не вызывает: публичный вишлист (S5a) — '
    '`GET /public/users/{user_id}/wishlist`, CTA подписки там нет.',
]

# Правило своего списка подписчиков (`followers_page`) для фичи 0024.
_FOLLOWERS_WORKFLOW = [
    'Свой список (`user_id` == свой id): у строки с `followed_by_me == false` — '
    'кнопка «В ответ» → `POST /follow/{id строки}` с '
    '`source=followers_follow_back`. `200` → строка в состоянии «вы подписаны» '
    'без перезагрузки списка; не-2xx/сеть → тост, кнопка в исходном состоянии.',
    'Строки с `followed_by_me == true` и чужой список подписчиков — без изменений.',
    'Пустой список (`[]`) — заглушка как сейчас.',
]


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


@router.post('/set_profile_image', responses=IMAGE_UPLOAD_RESPONSES)
def set_profile_image(
    image: UploadFile,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Загрузить свою аватарку (multipart, поле `image`).

    Размер и тип проверяются на сервере (см. коды 413/415).
    """
    content, _ = read_uploaded_image(image)
    save_profile_image_bytes(user, content, is_custom=True)
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


@router.get(
    '/users/{user_id}',
    response_model=AnnotatedOtherUserSchema,
    openapi_extra={'x-workflow': _PROFILE_WORKFLOW},
    responses={
        HTTP_404_NOT_FOUND: {
            'description': (
                'Юзера с таким id нет (удалил аккаунт или id испорчен). Экран '
                'показывает «не найдено», как сейчас.'
            ),
            'content': {'application/json': {'example': {'detail': 'User not found'}}},
        },
    },
)
def get_user(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Профиль другого юзера (S5): данные, подписки и отношение ко мне.

    `followed_by_me` — я подписан на него, `follows_me` — он на меня. По ним и по
    параметрам URL входа клиент выбирает CTA-блок и подпись кнопки подписки —
    правила в `x-workflow` операции (Swagger UI расширений не показывает).
    """
    user = db.scalars(select(User).where(User.id == user_id)).one_or_none()
    if not user:
        raise HTTPException(HTTP_404_NOT_FOUND, 'User not found')
    return get_annotated_users(db, current_user, [user])[0]


@router.post('/delete_own_account')
def delete_own_account(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    logger.info('Удаление аккаунта: {} {}', user.id, user.firebase_uid)
    delete_firebase_user(user.firebase_uid)
    db.delete(user)
    db.commit()


@router.get(
    '/users/{user_id}/followers',
    response_model=list[AnnotatedOtherUserSchema],
    openapi_extra={'x-workflow': _FOLLOWERS_WORKFLOW},
)
def user_followers(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Подписчики юзера (`followers_page`), каждый с отношением ко мне.

    Кнопка «В ответ» в своём списке — `x-workflow` операции.
    """
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


@router.post(
    '/follow/{follow_user_id}',
    openapi_extra={'x-push-payload': _NEW_FOLLOWER_PUSH_PAYLOAD},
    responses={
        200: {
            'description': (
                'Подписка оформлена (ребро создано) либо уже существовала — в обоих '
                'случаях `200`, действие идемпотентно. Тело ответа пустое: клиенту '
                'ничего читать не нужно. Событие в лог пишется только при реальном '
                'создании ребра (повторный follow — no-op, событие не пишется).'
            )
        },
        422: {
            'description': (
                'Невалидная форма запроса: `source` вне enum `FollowSource` либо '
                '`follow_user_id` не UUID. Метка источника best-effort, но битое '
                'значение отвергается сразу (не проглатывается в `null`) — это баг '
                'клиента. Пустое/отсутствующее тело валидно (не 422).'
            )
        },
    },
)
def follow_user(
    follow_user_id: UUID,
    body: FollowActionSchema | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Подписаться на юзера + залогировать событие с источником (инструментация графа).

    Идемпотентно: повторная подписка на того, на кого уже подписан, возвращает
    `200` и события не пишет. Метка `source` (тело опционально) — чистая аналитика,
    на результат действия не влияет; при пустом теле/старом клиенте пишется
    `source = null`. Событие и ребро создаются в одной транзакции. Существование
    таргета предполагается (валидный id из приложения); несуществующий — `5xx`
    (вне контракта). Побочно: подписанному придёт пуш о новом подписчике
    ежечасным кроном, одним сообщением за все подписки за час; payload —
    `x-push-payload` операции (Swagger UI расширений не показывает).
    """
    follow_user = db.execute(select(User).where(User.id == follow_user_id)).scalar_one()
    if follow_user in user.follows:
        return
    user.follows.append(follow_user)
    # Логируем факт подписки с источником (инструментация графа). Пишем только
    # при реальном создании ребра — повторный follow сюда не доходит.
    db.add(
        FollowEvent(
            actor_id=user.id,
            target_id=follow_user.id,
            action=FollowAction.follow,
            source=FollowEventSource(body.source.value)
            if body and body.source
            else None,
        )
    )
    db.commit()


@router.post(
    '/unfollow/{unfollow_user_id}',
    responses={
        200: {
            'description': (
                'Отписка выполнена (ребро удалено) либо его и не было — в обоих '
                'случаях `200`, действие идемпотентно. Тело ответа пустое. Событие '
                'в лог пишется только при реальном удалении ребра (отписка от '
                'неподписанного — no-op, событие не пишется).'
            )
        },
        422: {
            'description': (
                'Невалидная форма запроса: `source` вне enum `FollowSource` либо '
                '`unfollow_user_id` не UUID. Пустое/отсутствующее тело валидно.'
            )
        },
    },
)
def unfollow_user(
    unfollow_user_id: UUID,
    body: FollowActionSchema | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Отписаться от юзера + залогировать событие с источником (сигнал оттока связей).

    Идемпотентно: отписка от того, на кого не подписан, возвращает `200` и события
    не пишет. Метка `source` (тело опционально) — аналитика, на результат не влияет;
    при пустом теле пишется `source = null`. Отписки логируются наравне с подписками —
    таблица рёбер их теряет. Событие и удаление ребра — в одной транзакции.
    """
    unfollow_user = db.execute(
        select(User).where(User.id == unfollow_user_id)
    ).scalar_one()
    if unfollow_user not in user.follows:
        return
    user.follows.remove(unfollow_user)
    # Отписку тоже логируем — сигнал оттока связей, которого таблица рёбер не хранит.
    db.add(
        FollowEvent(
            actor_id=user.id,
            target_id=unfollow_user.id,
            action=FollowAction.unfollow,
            source=FollowEventSource(body.source.value)
            if body and body.source
            else None,
        )
    )
    db.commit()


@router.get('/possible_friends')
def possible_friends(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AnnotatedOtherUserSchema]:
    if not user.vk_friends_data:
        return []
    vk_friend_ids = [
        str(vk_friend_data['id']) for vk_friend_data in user.vk_friends_data
    ]
    query = (
        select(User)
        .where(User.vk_id.in_(vk_friend_ids))
        .where(~User.followed_by.any(User.id == user.id))
    )
    return get_annotated_users(db, user, query)


@router.post(
    '/item_info_from_page',
    responses={
        200: {
            'description': (
                'Страница товара разобрана: название/описание/картинка есть. '
                '`price` при этом может быть null и при `shop != null` (распродано/'
                'исчез/магазин цену не отдал) — это не ошибка, поле цены пустое, '
                'пометка «с WB» по `shop`.'
            )
        },
        400: {
            'description': (
                'Страницу товара разобрать не удалось (домен не поддерживается '
                'парсером превью, карточка не найдена, магазин/страница недоступны): '
                'превью нет ЦЕЛИКОМ — ни названия, ни цены (цена не запрашивается), '
                '`shop` неизвестен. Форма остаётся ручной без пометок; юзер '
                'заполняет её сам. Это не влияет на источник цены при сохранении: '
                'WB-ссылка + `price_edited = false` → бэк сам сходит в магазин и '
                'хотелка станет `shop` (ответ `POST /wishes` покажет `shop` и цену); '
                'ввёл цену руками → `manual`.'
            ),
            'content': {
                'application/json': {'example': {'detail': 'Ошибка получения данных'}}
            },
        },
        401: {
            'description': 'Нет или истёк токен авторизации.',
            'content': {
                'application/json': {'example': {'detail': 'Not authenticated'}}
            },
        },
    },
)
async def get_item_info_from_page(
    request_data: ItemInfoRequestSchema,
    user: User = Depends(get_current_user),
    store_client: httpx.Client = Depends(get_store_client),
) -> ItemInfoResponseSchema:
    """Превью товара по ссылке для автозаполнения формы хотелки (кнопка «применить»).

    Название, описание, картинка — как раньше. **Цена (фича 0011):** для ссылки на
    поддерживаемый магазин (`shop != null`) бэк отдельно делает свежий запрос цены
    со скидкой и наличия; цена best-effort — её отсутствие (`price = null`) не
    делает превью ошибкой. Для `?size=` — цена этого размера; без размера у
    многоразмерного товара — минимум среди размеров в наличии (`price_is_minimum`).
    Два разных сбоя: `400` — не разобралась сама страница (превью нет целиком);
    `200` с `price = null` — страница есть, цены нет. **Длительность:** разбор
    страницы может занять до ~30 с (WB: перебор хостов картинок), запрос цены — не
    дольше 10 с; клиентский таймаут ставьте от 40 с, спиннер на всё это время.
    Ничего не сохраняет: хотелка создаётся следующим `POST /wishes`, где бэк сам
    повторно возьмёт магазинную цену при `price_edited = false`.
    """
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
    return await run_in_threadpool(
        build_item_info, result, str(request_data.link), store_client
    )


@router.get(
    '/invite_link/',
    responses={
        200: {
            'description': (
                'Персональная инвайт-ссылка на список текущего юзера. Тело — голая '
                'строка-URL (deep link). Несёт `userId` (владелец списка) и метку '
                'атрибуции `ref` (id пригласившего = текущий юзер). Открывается '
                'гостем как публичная веб-страница вишлиста (S5a), залогиненным — '
                'как user_page (S5). Клиент-получатель обязан донести `ref` (и любые '
                'utm-параметры из URL) до момента регистрации и вернуть его в '
                '`attribution` auth-вызова: регистрация по ней подписывает новичка '
                'и пригласившего друг на друга (`mutual_follow_user_id` в ответе '
                'auth). `ref` в URL — маркер «открыт по ссылке шеринга»: '
                'залогиненному не подписанному на владельца показывается CTA-блок '
                'подписки (`x-workflow` у `GET /users/{user_id}`).'
            ),
            'content': {
                'application/json': {
                    'example': (
                        'https://hotelki.pro/user'
                        '?userId=7c9e6679-7425-40de-944b-e07fc1f90ae7'
                        '&ref=7c9e6679-7425-40de-944b-e07fc1f90ae7#'
                    )
                }
            },
        }
    },
)
def get_invite_link(user: User = Depends(get_current_user)) -> str:
    """Вернуть персональную инвайт-ссылку текущего юзера для шеринга своего списка.

    Ссылка содержит реф-метку `ref={my_id}` — основу реферальной атрибуции
    (см. фичу 0003). Форма ссылки и контракт получателя — в описании ответа 200.
    """
    return get_user_deep_link(user, ref=user)
