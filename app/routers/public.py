from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import HttpUrl
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.status import (
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_410_GONE,
    HTTP_429_TOO_MANY_REQUESTS,
    HTTP_501_NOT_IMPLEMENTED,
)

from app.constants import GUEST_COOKIE_MAX_AGE_SECONDS, GUEST_COOKIE_NAME
from app.db import User, Wish
from app.dependencies import PUBLIC_TAG, GuestCookie, get_db
from app.push_payloads import RESERVATION_PUSH_PAYLOAD
from app.schemas import (
    PublicBirthdaySchema,
    PublicOwnerSchema,
    PublicWishlistSchema,
    PublicWishSchema,
)

router = APIRouter(tags=[PUBLIC_TAG], prefix='/public')

_NO_STORE_HEADERS: dict[str, Any] = {
    'Cache-Control': {
        'description': (
            '`private, no-store` — ответ зависит от куки гостя (`reserved_by_me`), '
            'кешировать его нельзя ни прокси, ни браузеру.'
        ),
        'schema': {'type': 'string'},
    },
    'Vary': {
        'description': '`Cookie` — по той же причине.',
        'schema': {'type': 'string'},
    },
}

_SET_GUEST_COOKIE_HEADER: dict[str, Any] = {
    'Set-Cookie': {
        'description': (
            'Только если гость создан этим запросом (первый резерв в браузере): '
            f'`{GUEST_COOKIE_NAME}=<непрозрачно>; Path=/; Max-Age='
            f'{GUEST_COOKIE_MAX_AGE_SECONDS}; HttpOnly; Secure; SameSite=Lax`. '
            'Срок — год; повторные резервы куку не продлевают. Клиенту делать '
            'ничего не нужно.'
        ),
        'schema': {'type': 'string'},
    },
}

_USER_NOT_FOUND_RESPONSE: dict[str, Any] = {
    'description': (
        'Владельца списка нет: `user_id` не существует или юзер удалил аккаунт. '
        'Как `404` публичной страницы — показывайте страницу «не найдено».'
    ),
    'content': {'application/json': {'example': {'detail': 'Пользователь не найден'}}},
}

_WISH_GONE_RESPONSE: dict[str, Any] = {
    'description': (
        'Хотелки больше нет в этом списке: удалена, в архиве или `wish_id` не из '
        'списка `user_id`. «Этой хотелки больше нет»: карточку убрать, список '
        'перезапросить. Кука гостя не трогается; резерв на архивной хотелке '
        'сохраняется и вернётся в список после разархивации.'
    ),
    'content': {'application/json': {'example': {'detail': 'Хотелки больше нет'}}},
}

# Поведение гостя на S5a (фича 0018): структурно для аудитора и кодгена фронта
# (PROTOCOL.md §7).
_GUEST_WORKFLOW = [
    'Кука гостя работает только при вызове API с того же origin, что и SPA '
    '(прод: `https://hotelki.pro` + `/api/v1`; dev — через прокси dev-сервера на '
    'тот же origin). Cross-origin запрос куку не отправит и не сохранит: каждый '
    'резерв станет новым гостем, снять его будет нельзя. Обычный `fetch` на '
    "same-origin куку прикладывает сам (`credentials: 'same-origin'` — по "
    'умолчанию).',
    'Кнопки на карточке S5a: `is_reserved == false` → «Забронирую»; '
    '`reserved_by_me == true` → «Снять резерв»; `is_reserved && !reserved_by_me` — '
    'плашка «зарезервировано», кнопок нет.',
    'Гостевые вызовы идут без `Authorization` и не через общий обработчик '
    '«401 → перелогин»: `401` эти ручки не отдают никогда.',
    'Ответ `200` резерва/снятия — актуальная карточка (`PublicWishSchema`): '
    'заменить ею карточку, список не перезапрашивать.',
    'Блок входа (Google/VK) «Войдите, чтобы резерв не потерялся…» виден, пока в '
    'ЭТОМ списке есть хотелка с `reserved_by_me == true` — считается по текущему '
    'ответу списка/карточек, флага «уже показывали» на клиенте нет: F5 и повторный '
    'визит с резервом показывают его снова. Нет своих резервов в списке (в т.ч. '
    'после снятия последнего) — блока нет. «×» скрывает блок в этой вкладке для '
    'этого `user_id`, пока вкладка открыта (sessionStorage): новая вкладка и '
    'повторный визит покажут его снова. Отказ ничего не меняет — резерв держится.',
    '`409` → тост «уже забронировано», карточку перевести в «зарезервировано» '
    '(`is_reserved = true`, `reserved_by_me = false`).',
    '`410` → «этой хотелки больше нет», карточку убрать, перезапросить '
    '`GET /public/users/{user_id}/wishlist`.',
    '`404` → страница «не найдено», как у публичного списка.',
    '`429` (только резерв) → «Сейчас забронировать нельзя. Войдите в приложение, '
    'чтобы зарезервировать»; хотелка остаётся свободной.',
    '`403` (только снятие) → карточка остаётся «зарезервировано», кнопку снятия '
    'убрать (`reserved_by_me = false`).',
    'Не-2xx вне перечисленного → тост «Не удалось, попробуйте ещё раз», '
    'карточка без изменений.',
    'Ответа нет (сеть, таймаут) на «Забронирую» или «Снять резерв»: исход '
    'неизвестен — резерв мог создаться/сняться, а кука гостя могла не дойти. '
    'Перезапросить `GET /public/users/{user_id}/wishlist` и рисовать карточки по '
    'нему. Если после «Забронирую» карточка пришла «зарезервировано» не мной '
    '(`is_reserved && !reserved_by_me`) — тост «Не удалось подтвердить бронь. '
    'Список обновлён». Такой резерв ведёт себя как любой чужой: снять его из '
    'этого браузера нельзя.',
    'После входа на S5a (`POST /auth/firebase` / `POST /auth/vk/vkid`) резервы '
    'гостя уже на аккаунте, а экран становится списком владельца (S5) — '
    'перезапросить его данные как для S5.',
    'Вход не удался с ответом не-2xx: слияния не было — кука гостя на месте, '
    'резервы по-прежнему гостевые, карточки S5a верны как есть, перезапрос не '
    'нужен. Следующий успешный вход в этом браузере сольёт их.',
    'Ответа входа нет (сеть, таймаут) или VK-вход получил `200`, но '
    '`signInWithCustomToken` упал: состояние слияния неизвестно / слияние уже '
    'было — перезапросить этот список и рисовать по нему; подробности — '
    '`x-workflow` у `POST /auth/firebase` и `POST /auth/vk/vkid`.',
]


def _build_owner(user: User) -> PublicOwnerSchema:
    """Собирает публичные данные владельца, отрезая PII (email/телефон/год ДР)."""
    birthday = None
    if user.birth_date:
        birthday = PublicBirthdaySchema(
            day=user.birth_date.day, month=user.birth_date.month
        )
    return PublicOwnerSchema(
        id=user.id,
        display_name=user.display_name,
        photo_url=HttpUrl(user.photo_url) if user.photo_url else None,
        birthday=birthday,
    )


def _build_wish(wish: Wish) -> PublicWishSchema:
    """Собирает публичную хотелку: путь к картинке и булев флаг резерва без личности."""
    return PublicWishSchema(
        id=wish.id,
        name=wish.name,
        description=wish.description,
        price=int(wish.price) if wish.price is not None else None,
        price_is_minimum=wish.price_is_minimum,
        link=HttpUrl(wish.link) if wish.link else None,
        image_url=f'/media/wish_images/{wish.image}' if wish.image else None,
        is_reserved=wish.is_reserved,
        # Контракт 0018 до agreed: гостевых резервов ещё нет.
        reserved_by_me=False,
    )


@router.get(
    '/users/{user_id}/wishlist',
    response_model=PublicWishlistSchema,
    summary='Публичный вишлист владельца',
    # Публичная страница: гость без токена — снимаем глобальное требование ApiKey.
    openapi_extra={'security': [], 'x-workflow': _GUEST_WORKFLOW},
    responses={
        200: {
            'description': (
                'Вишлист найден. `wishes` может быть пустым (у владельца нет '
                'активных хотелок) — это не ошибка, показывайте заглушку + CTA.'
            ),
            'headers': _NO_STORE_HEADERS,
        },
        404: {
            'description': (
                'Пользователь с таким `user_id` не найден или удалён. '
                'Показывайте страницу 404, а не падение.'
            ),
            'content': {
                'application/json': {'example': {'detail': 'Пользователь не найден'}}
            },
        },
    },
)
def public_wishlist(
    user_id: UUID,
    response: Response,
    guest_id: GuestCookie = None,
    db: Session = Depends(get_db),
) -> PublicWishlistSchema:
    """Публичный вишлист для веб-страницы — открывается без авторизации и установки.

    Главный экран виральной петли: даритель-неюзер открывает расшаренную ссылку и
    сразу видит владельца и его активные желания, без требования установить приложение.

    **Что отдаётся:** владелец (имя, фото, день+месяц ДР — без года) и список активных
    хотелок; по каждой — булев `is_reserved` без личности дарителя.

    **Приватность (закон):** наружу не идут email, телефон и год рождения владельца;
    не раскрывается, кто зарезервировал; архивные хотелки исключены.

    **Состояния:** `200` со списком; `200` с пустым `wishes` (нет желаний); `404`
    (нет такого пользователя). В фазе 1 все списки публичны — приватного режима нет.

    **Гостевой резерв (фича 0018):** по куке гостя у каждой хотелки
    `reserved_by_me`; поэтому ответ не кешируется (`Cache-Control`, `Vary`). Резерв
    и снятие — `POST /public/users/{user_id}/wishes/{wish_id}/reserve` и
    `/cancel_reservation`; поведение карточек — `x-workflow` (Swagger UI
    расширений не показывает).
    """
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['Vary'] = 'Cookie'
    user = db.scalars(select(User).where(User.id == user_id)).one_or_none()
    if not user:
        raise HTTPException(HTTP_404_NOT_FOUND, 'Пользователь не найден')
    wishes = db.scalars(Wish.get_active_wish_query().where(Wish.user == user)).all()
    return PublicWishlistSchema(
        owner=_build_owner(user),
        wishes=[_build_wish(wish) for wish in wishes],
    )


@router.post(
    '/users/{user_id}/wishes/{wish_id}/reserve',
    response_model=PublicWishSchema,
    summary='Гостевой резерв хотелки',
    openapi_extra={
        'security': [],
        'x-workflow': _GUEST_WORKFLOW,
        'x-push-payload': RESERVATION_PUSH_PAYLOAD,
    },
    responses={
        200: {
            'description': (
                'Хотелка зарезервирована этим гостем — впервые или уже была его '
                '(повтор идемпотентен, состояние то же). Тело — актуальная '
                'карточка: `is_reserved = true`, `reserved_by_me = true`.'
            ),
            'headers': {**_SET_GUEST_COOKIE_HEADER, **_NO_STORE_HEADERS},
        },
        HTTP_404_NOT_FOUND: _USER_NOT_FOUND_RESPONSE,
        HTTP_409_CONFLICT: {
            'description': (
                'Уже зарезервирована кем-то другим (другим гостем или юзером '
                'приложения, в т.ч. одновременным тапом — резерв получает первый). '
                'Ничего не изменено, гость не создан.'
            ),
            'content': {
                'application/json': {'example': {'detail': 'Уже забронировано'}}
            },
        },
        HTTP_410_GONE: _WISH_GONE_RESPONSE,
        HTTP_429_TOO_MANY_REQUESTS: {
            'description': (
                'Сработала защита от злоупотреблений — один код на все лимиты '
                'гостевых резервов; какой именно, не раскрывается. Хотелка '
                'осталась свободной, гость не создан. Повтор позже может пройти; '
                'зарегистрированных лимит не касается.'
            ),
            'content': {
                'application/json': {
                    'example': {'detail': 'Сейчас забронировать нельзя'}
                }
            },
        },
    },
)
def guest_reserve_wish(
    user_id: UUID,
    wish_id: UUID,
    guest_id: GuestCookie = None,
    db: Session = Depends(get_db),
) -> PublicWishSchema:
    """Забронировать хотелку гостем с публичной страницы (S5a), без аккаунта.

    Без `Authorization`: гость узнаётся по куке; нет куки — гость создаётся
    этим запросом и получает куку в ответе (только при успехе). Заголовок
    `Authorization`, если прислан, игнорируется — резерв гостевой.

    Для владельца и других смотрящих гостевой резерв неотличим от обычного:
    хотелка «зарезервирована», личность скрыта; владельцу уходит тот же пуш
    «резерв», что и от резерва в приложении (`x-push-payload`). Резерв живёт,
    пока гость его не снимет или владелец не удалит хотелку; архивация резерв
    не снимает — после разархивации тот же гость снова видит `reserved_by_me`.
    После входа гостя в том же
    браузере резерв переходит на аккаунт (`POST /auth/firebase`,
    `POST /auth/vk/vkid`). Поведение карточки — `x-workflow`.
    """
    raise HTTPException(HTTP_501_NOT_IMPLEMENTED)


@router.post(
    '/users/{user_id}/wishes/{wish_id}/cancel_reservation',
    response_model=PublicWishSchema,
    summary='Снять гостевой резерв',
    openapi_extra={'security': [], 'x-workflow': _GUEST_WORKFLOW},
    responses={
        200: {
            'description': (
                'Резерв этого гостя снят, либо хотелка и так свободна (повтор '
                'идемпотентен). Тело — актуальная карточка: `is_reserved = false`, '
                '`reserved_by_me = false`.'
            ),
            'headers': _NO_STORE_HEADERS,
        },
        HTTP_403_FORBIDDEN: {
            'description': (
                'Резерв держит не этот гость: другой гость, юзер приложения, либо '
                'куки гостя нет/она устарела. Ничего не изменено.'
            ),
            'content': {
                'application/json': {'example': {'detail': 'Это не ваш резерв'}}
            },
        },
        HTTP_404_NOT_FOUND: _USER_NOT_FOUND_RESPONSE,
        HTTP_410_GONE: _WISH_GONE_RESPONSE,
    },
)
def guest_cancel_reservation(
    user_id: UUID,
    wish_id: UUID,
    guest_id: GuestCookie = None,
    db: Session = Depends(get_db),
) -> PublicWishSchema:
    """Снять свой гостевой резерв с публичной страницы (S5a).

    Без `Authorization`: гость узнаётся по куке. Снять можно только резерв,
    сделанный из этого браузера (другое устройство или встроенный браузер
    мессенджера — другой гость). Хотелка снова свободна для всех.
    """
    raise HTTPException(HTTP_501_NOT_IMPLEMENTED)
