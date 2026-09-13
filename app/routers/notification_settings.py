from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_422_UNPROCESSABLE_CONTENT

from app.constants import NotificationGroup
from app.db import User
from app.dependencies import NOTIFICATION_SETTINGS_TAG, get_current_user, get_db
from app.notification_settings import GROUP_TEXTS, group_states, set_group_enabled
from app.schemas import (
    NOTIFICATION_GROUP_KEY_PATTERN,
    NOTIFICATION_GROUPS_EXAMPLE_ALL_ON,
    NOTIFICATION_GROUPS_EXAMPLE_FRIENDS_OFF,
    NotificationGroupSchema,
    NotificationGroupToggleSchema,
    NotificationSettingsReadSchema,
)

router = APIRouter(tags=[NOTIFICATION_SETTINGS_TAG])


def build_settings(db: Session, user: User) -> NotificationSettingsReadSchema:
    return NotificationSettingsReadSchema(
        groups=[
            NotificationGroupSchema(
                key=group.value,
                title=GROUP_TEXTS[group].title,
                subtitle=GROUP_TEXTS[group].subtitle,
                enabled=enabled,
            )
            for group, enabled in group_states(db, user)
        ]
    )


def parse_notification_group(raw: str) -> NotificationGroup:
    """Путь `{group}` — строка, а не enum: старый клиент должен уметь переключить
    группу, появившуюся после его релиза. Проверяем сами и отвечаем в форме
    `HTTPValidationError`, как отвечал бы FastAPI на enum."""
    try:
        return NotificationGroup(raw)
    except ValueError:
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[
                {
                    'loc': ['path', 'group'],
                    'msg': 'Неизвестная группа уведомлений',
                    'type': 'value_error',
                }
            ],
        ) from None


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
| `prices` | дайджест по складу: «„X“ подешевела», «„X“ снова в наличии» (фича 0013) |
| `tips` | сезонные подборки (23 Февраля, 8 Марта, …), «твой список желаний пуст» |

Маппинг тип → группа живёт в коде бэка, у каждого типа ровно одна группа.
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
                'Все группы в порядке показа. Всегда полный список (5 групп в '
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
    return build_settings(db, user)


_TOGGLE_DESCRIPTION = f"""\
Переключить одну группу уведомлений; применяется сразу, без «сохранить».

Семантика `enabled = false`: бэк перестаёт **отправлять** пуши этой группы
(событийные и крон) — не «клиент скрывает». Антиспам-гварды выключенной
группы не расходуются: пропущенный из-за выключения пуш не пишется в лог
отправок и не сдвигает «раз в N дней»; включил обратно — получишь ближайший
повод, а не пропуск на месяц. In-app данные (радар ДР, счётчик резерва)
не меняются.

Идемпотентно: абсолютное значение, повтор с тем же `enabled` — `200`, состояние
то же. Гонка двух устройств — побеждает последний пришедший на бэк запрос
(last-write-wins), промежуточные не ошибки. Быстрые переключения с одного
устройства: ответы двух `PUT` подряд могут прийти в обратном порядке, поэтому
тело ответа применяйте к экрану, только если после этого запроса не уходил
более поздний `PUT`; иначе ответ игнорируйте — на экране остаётся последнее
желаемое состояние. Версий/ETag в ответе нет. Настройка аккаунта: действует на
всех устройствах и на вебе, переживает переустановку.

Сайд-эффект: при реальной смене положения (было ≠ стало) бэк пишет событие
«группа включена/выключена» для метрики «что раздражает»; повтор без смены
событие не пишет. Наружу событие не отдаётся.

Сайд-эффект группы `prices` (фича 0013): включение (`false` → `true`) сбрасывает
базу «видел» у всех активных WB-хотелок юзера на текущую цену карточки —
выключенная группа события не «копит», после включения придут только новые
падения цены относительно того, что юзер видит сейчас. На ответ не влияет.
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
        HTTP_422_UNPROCESSABLE_CONTENT: {
            'description': (
                'Невалидная форма запроса: `group` — не группа, которую бэк сейчас '
                'отдаёт в `GET` (в т.ч. группа, которой ещё нет, например `prices` '
                'до 0013), либо `enabled` не bool / тело пустое. Ничего не '
                'сохранено. Клиент, переключающий только присланные бэком `key`, '
                'сюда не попадает; показывайте тост «не удалось сохранить», '
                'тумблер верните.'
            ),
            'content': {
                'application/json': {
                    # Тот же $ref, что FastAPI ставит на свои 422: клиент
                    # разбирает ошибку одной формой.
                    'schema': {'$ref': '#/components/schemas/HTTPValidationError'},
                    'examples': {
                        'unknown_group': {
                            'summary': '`PUT …/prices` — такой группы бэк не отдаёт',
                            'value': {
                                'detail': [
                                    {
                                        'loc': ['path', 'group'],
                                        'msg': 'Неизвестная группа уведомлений',
                                        'type': 'value_error',
                                    }
                                ]
                            },
                        },
                        'bad_body': {
                            'summary': 'Тело без `enabled`',
                            'value': {
                                'detail': [
                                    {
                                        'loc': ['body', 'enabled'],
                                        'msg': 'Field required',
                                        'type': 'missing',
                                    }
                                ]
                            },
                        },
                    },
                }
            },
        },
    },
)
def toggle_notification_group(
    group: Annotated[
        str,
        Path(
            description=(
                '`key` группы из ответа `GET /users/me/notification_settings` — '
                'клиент передаёт как есть, не сверяя со своим списком. Значение, '
                'которого бэк сейчас не отдаёт, — `422`.'
            ),
            pattern=NOTIFICATION_GROUP_KEY_PATTERN,
            examples=['friends'],
        ),
    ],
    body: NotificationGroupToggleSchema,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationSettingsReadSchema:
    """Переключить одну группу — см. `_TOGGLE_DESCRIPTION`."""
    set_group_enabled(db, user, parse_notification_group(group), body.enabled)
    return build_settings(db, user)
