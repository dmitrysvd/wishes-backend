"""Выгрузка сырых данных myTracker (Export API) в локальную БД для джойна с
прод-таблицами. Как запускать и как джойнить — `.claude/skills/mytracker-export`.

Три вида данных (`--kind`): `installs` (установки), `sessions` (сессии),
`events` (кастомные события). Каждый — отдельный запрос к Export API: создать →
поллить статус → скачать csv.gz → залить в `mytracker.<kind>`.

Идемпотентность двух уровней:
- запрос: пара (kind, окно дат) регистрируется в `mytracker.export_request`;
  повторный запуск с тем же окном переиспользует незавершённый запрос (не
  плодит дубли в очереди myTracker — там лимит одновременных запросов) и не
  создаёт новый, если прошлый успешно залит (нужен `--force`);
- строки: естественный ключ (устройство + время события [+ имя события]) с
  `ON CONFLICT DO NOTHING` — повторная заливка того же окна не дублирует.

Ключи API — `MYTRACKER_API_USER_ID` / `MYTRACKER_API_SECRET` в `.env`.
Квоты myTracker: 100 запросов/мин на пользователя, на 429 ждём `resetIn`.
"""

import argparse
import csv
import gzip
import io
import sys
import time
from base64 import b64encode
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha1
from hmac import new as hmac_new
from typing import Any
from urllib.parse import quote, urlencode
from uuid import UUID

import httpx
from sqlalchemy import Connection, create_engine, text

from app.config import settings
from app.logging import logger

API_BASE = 'https://tracker.my.com/api/raw/v1/export'
SCHEMA = 'mytracker'
# Пауза между опросами статуса: квота 100 запросов/мин, экспорт идёт минуты.
POLL_INTERVAL_SECONDS = 15
# Селекторы, общие для всех видов: кто (устройство, наш user.id), когда, версия.
COMMON_SELECTORS = (
    'idDevice',
    'idProfile',
    'customUserId',
    'tsEvent',
    'idAppVersionTitle',
    'idOsVersionTitle',
)


@dataclass(frozen=True)
class ExportKind:
    """Вид выгрузки: событие Export API, селекторы и таблица назначения."""

    name: str
    event: str
    selectors: tuple[str, ...]
    # Колонки естественного ключа таблицы (для ON CONFLICT).
    key: tuple[str, ...]
    # selector → колонка таблицы (кроме общих, см. `_row_from_record`).
    extra: tuple[tuple[str, str], ...]


KINDS: dict[str, ExportKind] = {
    'installs': ExportKind(
        name='installs',
        event='installs',
        selectors=COMMON_SELECTORS
        + (
            'idDeviceModelTitle',
            'idManufacturerTitle',
            'idCountryISOAlpha2',
            'idTrackerSdkVersionTitle',
        ),
        key=('id_profile', 'event_at'),
        extra=(
            ('idDeviceModelTitle', 'device_model'),
            ('idManufacturerTitle', 'manufacturer'),
            ('idCountryISOAlpha2', 'country'),
            ('idTrackerSdkVersionTitle', 'sdk_version'),
        ),
    ),
    'sessions': ExportKind(
        name='sessions',
        event='sessions',
        selectors=COMMON_SELECTORS + ('duration',),
        key=('id_profile', 'event_at'),
        extra=(('duration', 'duration'),),
    ),
    'events': ExportKind(
        name='events',
        event='customEvents',
        selectors=COMMON_SELECTORS + ('eventName', 'eventValue'),
        key=('id_profile', 'event_at', 'event_name'),
        extra=(('eventName', 'event_name'), ('eventValue', 'event_value')),
    ),
}

DDL = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
CREATE TABLE IF NOT EXISTS {SCHEMA}.export_request (
    id_raw_export bigint PRIMARY KEY,
    kind text NOT NULL,
    date_from date NOT NULL,
    date_to date NOT NULL,
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    rows_loaded integer
);
CREATE TABLE IF NOT EXISTS {SCHEMA}.installs (
    id_profile text NOT NULL,
    id_device text,
    custom_user_id text,
    user_id uuid,
    event_at timestamptz NOT NULL,
    installed_at timestamptz,
    app_version text,
    os_version text,
    device_model text,
    manufacturer text,
    country text,
    sdk_version text,
    PRIMARY KEY (id_profile, event_at)
);
CREATE TABLE IF NOT EXISTS {SCHEMA}.sessions (
    id_profile text NOT NULL,
    id_device text,
    custom_user_id text,
    user_id uuid,
    event_at timestamptz NOT NULL,
    duration integer,
    app_version text,
    os_version text,
    PRIMARY KEY (id_profile, event_at)
);
CREATE TABLE IF NOT EXISTS {SCHEMA}.events (
    id_profile text NOT NULL,
    id_device text,
    custom_user_id text,
    user_id uuid,
    event_at timestamptz NOT NULL,
    event_name text NOT NULL,
    event_value text,
    app_version text,
    os_version text,
    PRIMARY KEY (id_profile, event_at, event_name)
);
CREATE INDEX IF NOT EXISTS sessions_user_id_idx ON {SCHEMA}.sessions (user_id);
CREATE INDEX IF NOT EXISTS events_user_id_idx ON {SCHEMA}.events (user_id);
CREATE INDEX IF NOT EXISTS installs_user_id_idx ON {SCHEMA}.installs (user_id);
"""


# --- Auth ------------------------------------------------------------------


def auth_header(
    api_user_id: str, secret: str, method: str, url: str, body: str = ''
) -> str:
    """`Authorization: AuthHMAC <id>:<base64(HMAC-SHA1(method&url&body))>` —
    схема из документации myTracker; url и body percent-кодируются с `safe='~'`."""
    baseline = f'{method.upper()}&{quote(url, safe="~")}&{quote(body, safe="~")}'
    digest = hmac_new(secret.encode(), baseline.encode(), sha1).digest()
    return f'AuthHMAC {api_user_id}:{b64encode(digest).decode()}'


# --- API -------------------------------------------------------------------


@dataclass(frozen=True)
class ExportStatus:
    status: str
    files: tuple[str, ...] = ()
    progress: str | None = None
    error: str | None = None

    @property
    def done(self) -> bool:
        return self.status == 'Success!'

    @property
    def failed(self) -> bool:
        # 'Error occurred' — можно повторить; 'User error occurred' — нет.
        return self.status in (
            'Error occurred',
            'User error occurred',
            'Canceled by user',
        )


def parse_create_response(payload: dict[str, Any]) -> int:
    """`{"code":200,"data":{"idRawExport":"2"}}` → 2. Иначе — ошибка с текстом API."""
    if payload.get('code') != 200:
        raise RuntimeError(f'myTracker create: {payload.get("message")}: {payload}')
    return int(payload['data']['idRawExport'])


def parse_status_response(payload: dict[str, Any]) -> ExportStatus:
    if payload.get('code') != 200:
        raise RuntimeError(f'myTracker get: {payload.get("message")}: {payload}')
    data = payload['data']
    return ExportStatus(
        status=data['status'],
        files=tuple(f['link'] for f in data.get('files', [])),
        progress=data.get('progress'),
        error=data.get('errorMessage'),
    )


class MyTrackerClient:
    """Тонкий клиент Export API: подпись, квоты (429 → ждём `resetIn`)."""

    def __init__(self, api_user_id: str, secret: str, http: httpx.Client) -> None:
        self._id = api_user_id
        self._secret = secret
        self._http = http

    def _request(self, method: str, url: str, body: str = '') -> dict[str, Any]:
        while True:
            headers = {
                'Authorization': auth_header(self._id, self._secret, method, url, body)
            }
            if body:
                headers['Content-Type'] = 'application/x-www-form-urlencoded'
            response = self._http.request(method, url, headers=headers, content=body)
            if response.status_code == 429:
                wait = _quota_reset_in(response.json())
                logger.warning('myTracker: квота исчерпана, ждём {} с', wait)
                time.sleep(wait)
                continue
            return response.json()

    def create(self, kind: ExportKind, date_from: date, date_to: date) -> int:
        body = urlencode(
            {
                'event': kind.event,
                'selectors': ','.join(kind.selectors),
                'dateFrom': date_from.isoformat(),
                'dateTo': date_to.isoformat(),
                # tsEvent — unix, но dtEvent/границы окна зависят от таймзоны.
                'timezone': 'UTC',
            }
        )
        return parse_create_response(
            self._request('POST', f'{API_BASE}/create.json', body)
        )

    def status(self, id_raw_export: int) -> ExportStatus:
        return parse_status_response(
            self._request('GET', f'{API_BASE}/get.json?idRawExport={id_raw_export}')
        )

    def download(self, link: str) -> bytes:
        response = self._http.get(
            link,
            headers={'Authorization': auth_header(self._id, self._secret, 'GET', link)},
        )
        response.raise_for_status()
        return response.content


def _quota_reset_in(payload: dict[str, Any]) -> int:
    quotas = payload.get('data', {}).get('error', {}).get('info', {}).get('quotas', [])
    waits = [int(q['resetIn']) for q in quotas if 'resetIn' in q]
    return max(waits, default=60) + 1


# --- CSV → строки ----------------------------------------------------------


def read_csv_gz(content: bytes) -> list[dict[str, str]]:
    """csv.gz от myTracker → список записей по заголовку (имена = селекторы)."""
    text_io = io.TextIOWrapper(
        gzip.GzipFile(fileobj=io.BytesIO(content)), encoding='utf-8'
    )
    return list(csv.DictReader(text_io))


def _ts(value: str | None) -> datetime | None:
    return datetime.fromtimestamp(int(value), tz=timezone.utc) if value else None


def _uuid_or_none(value: str | None) -> UUID | None:
    try:
        return UUID(value) if value else None
    except ValueError:
        return None


def _row_from_record(kind: ExportKind, rec: dict[str, str]) -> dict[str, Any]:
    row: dict[str, Any] = {
        'id_profile': rec.get('idProfile') or rec.get('idDevice') or '',
        'id_device': rec.get('idDevice') or None,
        'custom_user_id': rec.get('customUserId') or None,
        # customUserId клиент ставит = наш user.id; чужой формат → NULL, чтобы
        # джойн с `user` был прямым по uuid.
        'user_id': _uuid_or_none(rec.get('customUserId')),
        'event_at': _ts(rec.get('tsEvent')),
        'app_version': rec.get('idAppVersionTitle') or None,
        'os_version': rec.get('idOsVersionTitle') or None,
    }
    if kind.name == 'installs':
        # У события «установка» время события и есть время установки;
        # селектора tsInstall у installs нет.
        row['installed_at'] = row['event_at']
    for selector, column in kind.extra:
        value = rec.get(selector)
        row[column] = int(value) if column == 'duration' and value else (value or None)
    return row


def rows_for_table(
    kind: ExportKind, records: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """Записи csv → строки таблицы; без времени события или id — пропускаем
    (такие не лягут в ключ), их число — в лог."""
    rows = [_row_from_record(kind, rec) for rec in records]
    good = [r for r in rows if r['event_at'] is not None and r['id_profile']]
    if len(good) != len(rows):
        logger.warning(
            '{}: пропущено {} записей без ключа', kind.name, len(rows) - len(good)
        )
    return good


# --- БД --------------------------------------------------------------------


def ensure_schema(conn: Connection) -> None:
    for statement in DDL.strip().split(';'):
        if statement.strip():
            conn.execute(text(statement))


def upsert_rows(conn: Connection, kind: ExportKind, rows: list[dict[str, Any]]) -> int:
    """Вставить строки, дубли по естественному ключу пропустить. Возвращает
    число реально вставленных."""
    if not rows:
        return 0
    columns = list(rows[0])
    sql = text(
        f'INSERT INTO {SCHEMA}.{kind.name} ({", ".join(columns)}) '
        f'VALUES ({", ".join(":" + c for c in columns)}) '
        f'ON CONFLICT ({", ".join(kind.key)}) DO NOTHING'
    )
    inserted = 0
    for row in rows:
        inserted += conn.execute(sql, row).rowcount
    return inserted


def find_request(
    conn: Connection, kind: ExportKind, date_from: date, date_to: date
) -> tuple[int, str] | None:
    """Последний зарегистрированный запрос с тем же окном: (id, status)."""
    row = conn.execute(
        text(
            f'SELECT id_raw_export, status FROM {SCHEMA}.export_request '
            'WHERE kind = :kind AND date_from = :date_from AND date_to = :date_to '
            'ORDER BY created_at DESC LIMIT 1'
        ),
        {'kind': kind.name, 'date_from': date_from, 'date_to': date_to},
    ).first()
    return (row[0], row[1]) if row else None


def save_request(
    conn: Connection,
    id_raw_export: int,
    kind: ExportKind,
    date_from: date,
    date_to: date,
    status: str,
    rows_loaded: int | None = None,
) -> None:
    conn.execute(
        text(
            f'INSERT INTO {SCHEMA}.export_request '
            '(id_raw_export, kind, date_from, date_to, status, finished_at, '
            'rows_loaded) VALUES (:id, :kind, :date_from, :date_to, :status, '
            ':finished_at, :rows_loaded) '
            'ON CONFLICT (id_raw_export) DO UPDATE SET status = EXCLUDED.status, '
            'finished_at = EXCLUDED.finished_at, rows_loaded = EXCLUDED.rows_loaded'
        ),
        {
            'id': id_raw_export,
            'kind': kind.name,
            'date_from': date_from,
            'date_to': date_to,
            'status': status,
            'finished_at': datetime.now(timezone.utc)
            if status in ('loaded', 'failed')
            else None,
            'rows_loaded': rows_loaded,
        },
    )


# --- Сценарий --------------------------------------------------------------


def run_export(
    client: MyTrackerClient,
    conn: Connection,
    kind: ExportKind,
    date_from: date,
    date_to: date,
    *,
    force: bool = False,
    poll_interval: float = POLL_INTERVAL_SECONDS,
) -> int:
    """Полный цикл для одного окна. Возвращает число вставленных строк.

    Статусы в `export_request`: `pending` (создан/в работе у myTracker),
    `loaded` (файлы залиты), `failed` (myTracker отказал). Незавершённый
    `pending` переиспользуется, `loaded` без `--force` — пропуск.
    """
    ensure_schema(conn)
    existing = find_request(conn, kind, date_from, date_to)
    if existing and existing[1] == 'loaded' and not force:
        logger.info(
            '{} {}..{}: уже залито (запрос {}), --force для повтора',
            kind.name,
            date_from,
            date_to,
            existing[0],
        )
        return 0
    if existing and existing[1] == 'pending':
        id_raw_export = existing[0]
        logger.info(
            '{} {}..{}: продолжаем запрос {}',
            kind.name,
            date_from,
            date_to,
            id_raw_export,
        )
    else:
        id_raw_export = client.create(kind, date_from, date_to)
        save_request(conn, id_raw_export, kind, date_from, date_to, 'pending')
        conn.commit()
        logger.info(
            '{} {}..{}: создан запрос {}', kind.name, date_from, date_to, id_raw_export
        )

    while True:
        status = client.status(id_raw_export)
        if status.done:
            break
        if status.failed:
            save_request(conn, id_raw_export, kind, date_from, date_to, 'failed')
            conn.commit()
            raise RuntimeError(
                f'myTracker: запрос {id_raw_export} — {status.status}: {status.error}'
            )
        logger.info(
            'запрос {}: {} {}', id_raw_export, status.status, status.progress or ''
        )
        time.sleep(poll_interval)

    inserted = 0
    for link in status.files:
        records = read_csv_gz(client.download(link))
        inserted += upsert_rows(conn, kind, rows_for_table(kind, records))
    save_request(conn, id_raw_export, kind, date_from, date_to, 'loaded', inserted)
    conn.commit()
    logger.info(
        '{} {}..{}: вставлено {} строк из {} файлов',
        kind.name,
        date_from,
        date_to,
        inserted,
        len(status.files),
    )
    return inserted


def build_client(http: httpx.Client | None = None) -> MyTrackerClient:
    if not settings.MYTRACKER_API_USER_ID or not settings.MYTRACKER_API_SECRET:
        raise SystemExit('Нужны MYTRACKER_API_USER_ID и MYTRACKER_API_SECRET в .env')
    return MyTrackerClient(
        settings.MYTRACKER_API_USER_ID,
        settings.MYTRACKER_API_SECRET,
        http or httpx.Client(timeout=60),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument(
        '--kind',
        choices=sorted(KINDS),
        required=True,
        action='append',
        help='Вид данных; флаг можно повторять',
    )
    parser.add_argument(
        '--from', dest='date_from', type=date.fromisoformat, required=True
    )
    parser.add_argument('--to', dest='date_to', type=date.fromisoformat, required=True)
    parser.add_argument(
        '--force', action='store_true', help='Перезапросить уже залитое окно'
    )
    parser.add_argument('--poll-interval', type=float, default=POLL_INTERVAL_SECONDS)
    args = parser.parse_args(argv)

    client = build_client()
    engine = create_engine(settings.DATABASE_URL)
    total = 0
    with engine.connect() as conn:
        for kind_name in args.kind:
            total += run_export(
                client,
                conn,
                KINDS[kind_name],
                args.date_from,
                args.date_to,
                force=args.force,
                poll_interval=args.poll_interval,
            )
    print(f'Вставлено строк: {total}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
