from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_501_NOT_IMPLEMENTED

from app.constants import NotificationGroup
from app.db import User
from app.dependencies import NOTIFICATION_SETTINGS_TAG, get_current_user, get_db
from app.schemas import (
    NOTIFICATION_GROUPS_EXAMPLE_ALL_ON,
    NOTIFICATION_GROUPS_EXAMPLE_FRIENDS_OFF,
    NotificationGroupToggleSchema,
    NotificationSettingsReadSchema,
)

router = APIRouter(tags=[NOTIFICATION_SETTINGS_TAG])

_AUTH_RESPONSE: dict[int | str, dict[str, Any]] = {
    HTTP_401_UNAUTHORIZED: {
        'description': (
            'Нет или истёк токен авторизации. Экран внутри авторизованной зоны — '
            'гостя сюда не пускайте; истёкший токен обновляйте как везде.'
        ),
        'content': {'application/json': {'example': {'detail': 'Not authenticated'}}},
    },
}

# Таблица «тип пуша → группа» — общий хвост description обеих операций.
_GROUP_MAPPING_DOC = """
| Группа (`key`) | Какие пуши в ней |
|---|---|
| `reservation` | кто-то зарезервировал твою хотелку |
| `friends` | «у вас новый подписчик» (часовой дайджест), «X обновил список желаний» |
| `birthdays` | «скоро твой день рождения», «скоро день рождения у X» |
| `tips` | сезонные подборки (23 Февраля, 8 Марта, …), «твой список желаний пуст» |

Маппинг тип → группа живёт в коде бэка, у каждого типа ровно одна группа; группы
«Цены и наличие» пока нет — появится вместе с фичей 0013 как новая строка списка.
"""


_READ_DESCRIPTION = f"""\
Экран «Уведомления»: список групп с названиями, подписями и состоянием.

Читайте при каждом входе на экран, не из кэша: состояние — аккаунта, его
могли поменять с другого устройства или с веба. Состав и порядок групп,
тексты `title`/`subtitle` задаёт бэк — клиент только рисует; незнакомый
`key` рисуется как обычная строка и переключается той же ручкой.

Дефолт для всех групп — `enabled = true` (opt-out): существующий юзер без
сохранённых настроек получает все `true`. Пустой `groups` бэк не отдаёт.
Сайд-эффектов нет.
{_GROUP_MAPPING_DOC}"""


@router.get(
    '/users/me/notification_settings',
    response_model=NotificationSettingsReadSchema,
    description=_READ_DESCRIPTION,
    responses={
        200: {
            'description': (
                'Все группы в порядке показа. Всегда полный список (4 группы в '
                'этой версии) — и у юзера, который ничего не менял (все `true`), '
                'и у юзера без push-токена: настройка принадлежит аккаунту, а не '
                'устройству, разрешение ОС бэк не знает и в ответе не отражает.'
            ),
            'content': {
                'application/json': {
                    'examples': {
                        'default': {
                            'summary': 'Ничего не менял — все группы включены',
                            'value': {'groups': NOTIFICATION_GROUPS_EXAMPLE_ALL_ON},
                        },
                        'friends_off': {
                            'summary': 'Выключил «Друзья» (на любом устройстве)',
                            'value': {
                                'groups': NOTIFICATION_GROUPS_EXAMPLE_FRIENDS_OFF
                            },
                        },
                    }
                }
            },
        },
        **_AUTH_RESPONSE,
    },
)
def read_notification_settings(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationSettingsReadSchema:
    """Экран «Уведомления» — см. `_READ_DESCRIPTION`."""
    raise HTTPException(status_code=HTTP_501_NOT_IMPLEMENTED)


_TOGGLE_DESCRIPTION = f"""\
Переключить одну группу уведомлений; применяется сразу, без «сохранить».

Семантика `enabled = false`: бэк перестаёт **отправлять** пуши этой группы
(событийные и крон) — не «клиент скрывает». Антиспам-гварды выключенной
группы не расходуются: пропущенный из-за выключения пуш не пишется в лог
отправок и не сдвигает «раз в N дней»; включил обратно — получишь ближайший
повод, а не пропуск на месяц. In-app данные (радар ДР, счётчик резерва)
не меняются.

Идемпотентно: абсолютное значение, повтор с тем же `enabled` — `200`, состояние
то же. Гонка двух устройств — побеждает последний пришедший запрос
(last-write-wins), промежуточные не ошибки. Настройка аккаунта: действует на
всех устройствах и на вебе, переживает переустановку.

Сайд-эффект: при реальной смене положения (было ≠ стало) бэк пишет событие
«группа включена/выключена» для метрики «что раздражает»; повтор без смены
событие не пишет. Наружу событие не отдаётся.
{_GROUP_MAPPING_DOC}"""


@router.put(
    '/users/me/notification_settings/{group}',
    response_model=NotificationSettingsReadSchema,
    description=_TOGGLE_DESCRIPTION,
    responses={
        200: {
            'description': (
                'Настройка сохранена (или уже была такой — повтор безвреден). '
                'В теле — актуальный полный список групп, как в `GET`: '
                'перерисуйте экран по нему, чтобы подхватить и изменения с '
                'других устройств без второго запроса.'
            ),
            'content': {
                'application/json': {
                    'examples': {
                        'friends_off': {
                            'summary': '`PUT …/friends {"enabled": false}`',
                            'value': {
                                'groups': NOTIFICATION_GROUPS_EXAMPLE_FRIENDS_OFF
                            },
                        },
                        'friends_on_again': {
                            'summary': '`PUT …/friends {"enabled": true}` — обратно',
                            'value': {'groups': NOTIFICATION_GROUPS_EXAMPLE_ALL_ON},
                        },
                    }
                }
            },
        },
        **_AUTH_RESPONSE,
        422: {
            'description': (
                'Невалидная форма запроса: `group` вне enum `NotificationGroup` '
                '(в т.ч. группа, которой ещё нет, например `prices` до 0013) либо '
                '`enabled` не bool / тело пустое. Ничего не сохранено. Клиент, '
                'рисующий только присланные бэком `key`, сюда не попадает; '
                'показывайте тост «не удалось сохранить», тумблер верните.'
            ),
        },
    },
)
def toggle_notification_group(
    group: Annotated[
        NotificationGroup,
        Path(
            description=(
                '`key` группы из ответа `GET /users/me/notification_settings`. '
                'Значение вне enum — `422`.'
            )
        ),
    ],
    body: NotificationGroupToggleSchema,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationSettingsReadSchema:
    """Переключить одну группу — см. `_TOGGLE_DESCRIPTION`."""
    raise HTTPException(status_code=HTTP_501_NOT_IMPLEMENTED)
