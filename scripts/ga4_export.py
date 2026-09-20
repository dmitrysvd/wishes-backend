"""Срез Firebase/GA4 Analytics (Data API) в markdown для usage-analysis. Как
запускать и как читать — `.claude/skills/ga4-export`.

Один прогон = несколько отчётов (`REPORTS`) за период: активные по платформе,
события по имени, `login` по методу/автовходу, `screen_view` по экранам. Каждый
отчёт — отдельный `runReport`; ошибка одного (например, незарегистрированный
custom dimension) печатается под его заголовком и не роняет остальные.

`--realtime` — те же отчёты через `runRealtimeReport` (последние 30 минут):
стандартные отчёты отстают на сутки-двое, для проверки «события пошли после
деплоя» нужен именно он.

Ключи: `GA4_PROPERTY_ID` (числовой id property) и `GA4_KEY_PATH` (json
сервисного аккаунта с ролью Viewer на property; без него — `FIREBASE_KEY_PATH`).
"""

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.logging import logger

API_BASE = 'https://analyticsdata.googleapis.com/v1beta'
SCOPE = 'https://www.googleapis.com/auth/analytics.readonly'
# Realtime API знает только эти метрики; остальные из отчёта отбрасываем.
REALTIME_METRICS = frozenset({'activeUsers', 'eventCount', 'screenPageViews'})
ROW_LIMIT = 250


@dataclass(frozen=True)
class Report:
    """Один отчёт: заголовок в markdown, измерения, метрики, фильтр по событию."""

    title: str
    dimensions: tuple[str, ...]
    metrics: tuple[str, ...]
    # Имя события, которым ограничен отчёт (`eventName == …`); None — все.
    event: str | None = None
    note: str = ''

    def body(self, *, realtime: bool) -> dict[str, Any]:
        metrics = self.metrics
        if realtime:
            metrics = tuple(m for m in metrics if m in REALTIME_METRICS)
        payload: dict[str, Any] = {
            'dimensions': [{'name': d} for d in self.dimensions],
            'metrics': [{'name': m} for m in metrics],
            'limit': ROW_LIMIT,
            'orderBys': [{'metric': {'metricName': metrics[0]}, 'desc': True}],
        }
        if self.event:
            payload['dimensionFilter'] = {
                'filter': {
                    'fieldName': 'eventName',
                    'stringFilter': {'value': self.event},
                }
            }
        return payload


REPORTS: tuple[Report, ...] = (
    Report(
        title='Активные пользователи по платформе',
        dimensions=('platform',),
        metrics=('activeUsers', 'newUsers', 'sessions'),
        note='iOS-PWA считается платформой `web`; MyTracker сюда не входит.',
    ),
    Report(
        title='События по имени',
        dimensions=('eventName', 'platform'),
        metrics=('eventCount', 'totalUsers'),
    ),
    Report(
        title='`login` по методу и автовходу',
        dimensions=('customEvent:method', 'customEvent:is_auto', 'platform'),
        metrics=('eventCount', 'totalUsers'),
        event='login',
        note=(
            '`method`/`is_auto` — custom dimensions; в GA4 их надо зарегистрировать '
            '(Admin → Custom definitions), данные копятся с момента регистрации.'
        ),
    ),
    Report(
        title='`screen_view` по экранам',
        dimensions=('unifiedScreenName', 'platform'),
        metrics=('eventCount', 'totalUsers'),
        event='screen_view',
        note='Имена экранов — из индекса `PRODUCT.md` (фича 0022).',
    ),
)


@dataclass(frozen=True)
class Table:
    """Разобранный ответ API: заголовки и строки (значения как отдал API)."""

    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    # Сколько строк всего у GA4; больше `len(rows)` — упёрлись в ROW_LIMIT.
    row_count: int = 0


class GA4Error(RuntimeError):
    """Ответ API не 200: текст ошибки Google как есть."""


def service_account_token(key_path: Path) -> str:
    """OAuth2-токен сервисного аккаунта по json-ключу (google-auth ставится вместе
    с firebase-admin). Импорт внутри: тесты обходятся без ключа и сети."""
    from google.auth.transport.requests import Request
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(str(key_path), scopes=[SCOPE])
    creds.refresh(Request())
    return str(creds.token)


def parse_report(payload: dict[str, Any]) -> Table:
    """`{dimensionHeaders, metricHeaders, rows: [{dimensionValues, metricValues}]}`
    → таблица. `rows` отсутствует, если данных нет."""
    headers = tuple(h['name'] for h in payload.get('dimensionHeaders', [])) + tuple(
        h['name'] for h in payload.get('metricHeaders', [])
    )
    rows = tuple(
        tuple(v['value'] for v in row.get('dimensionValues', []))
        + tuple(v['value'] for v in row.get('metricValues', []))
        for row in payload.get('rows', [])
    )
    return Table(headers=headers, rows=rows, row_count=int(payload.get('rowCount', 0)))


class GA4Client:
    """Тонкий клиент Data API: один property, готовый bearer-токен."""

    def __init__(self, property_id: str, token: str, http: httpx.Client) -> None:
        self.property = f'properties/{property_id}'
        self._token = token
        self._http = http

    def _run(self, method: str, body: dict[str, Any]) -> Table:
        response = self._http.post(
            f'{API_BASE}/{self.property}:{method}',
            json=body,
            headers={'Authorization': f'Bearer {self._token}'},
        )
        if response.status_code != 200:
            error = response.json().get('error', {})
            raise GA4Error(
                f'{error.get("status", response.status_code)}: '
                f'{error.get("message", response.text)}'
            )
        return parse_report(response.json())

    def run_report(self, report: Report, date_from: date, date_to: date) -> Table:
        body = report.body(realtime=False)
        body['dateRanges'] = [
            {'startDate': date_from.isoformat(), 'endDate': date_to.isoformat()}
        ]
        return self._run('runReport', body)

    def run_realtime(self, report: Report) -> Table:
        return self._run('runRealtimeReport', report.body(realtime=True))


# --- Markdown ------------------------------------------------------------


def _cell(value: str) -> str:
    return value.replace('|', '\\|') or '(not set)'


def render_table(table: Table) -> str:
    if not table.rows:
        return '_нет данных_'
    lines = [
        '| ' + ' | '.join(table.headers) + ' |',
        '|' + '---|' * len(table.headers),
        *('| ' + ' | '.join(_cell(v) for v in row) + ' |' for row in table.rows),
    ]
    if table.row_count > len(table.rows):
        lines.append(f'\n_показано {len(table.rows)} из {table.row_count} строк_')
    return '\n'.join(lines)


@dataclass
class Rendered:
    """Собранный markdown-документ: заголовок, затем секции по отчётам."""

    lines: list[str] = field(default_factory=list)

    def section(self, report: Report, body: str) -> None:
        self.lines += [f'### {report.title}', '']
        if report.note:
            self.lines += [f'_{report.note}_', '']
        self.lines += [body, '']

    def text(self) -> str:
        return '\n'.join(self.lines).rstrip() + '\n'


def build_document(
    client: GA4Client,
    reports: tuple[Report, ...],
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    realtime: bool = False,
) -> str:
    fetch: Callable[[Report], Table]
    if realtime:
        period = 'последние 30 минут (Realtime)'
        fetch = client.run_realtime
    else:
        if date_from is None or date_to is None:
            raise ValueError('нужны date_from и date_to либо realtime=True')
        period = f'{date_from} — {date_to} (стандартные отчёты отстают на 1–2 дня)'
        fetch = partial(client.run_report, date_from=date_from, date_to=date_to)
    doc = Rendered(
        [
            f'## GA4 {client.property}: {period}',
            '',
            f'_снято {datetime.now(UTC):%Y-%m-%d %H:%M} UTC_',
            '',
        ]
    )
    for report in reports:
        try:
            table = fetch(report)
        except GA4Error as exc:
            logger.warning('GA4 «{}»: {}', report.title, exc)
            doc.section(report, f'⚠️ {exc}')
            continue
        doc.section(report, render_table(table))
    return doc.text()


def build_client(http: httpx.Client | None = None) -> GA4Client:
    if not settings.GA4_PROPERTY_ID:
        raise SystemExit('Нужен GA4_PROPERTY_ID в .env (числовой id property)')
    key_path = settings.GA4_KEY_PATH or settings.FIREBASE_KEY_PATH
    if not key_path.exists():
        raise SystemExit(f'Нет ключа сервисного аккаунта: {key_path}')
    return GA4Client(
        settings.GA4_PROPERTY_ID,
        service_account_token(key_path),
        http or httpx.Client(timeout=60),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--from', dest='date_from', type=date.fromisoformat)
    parser.add_argument('--to', dest='date_to', type=date.fromisoformat)
    parser.add_argument(
        '--realtime',
        action='store_true',
        help='Последние 30 минут через Realtime API вместо периода',
    )
    args = parser.parse_args(argv)
    if not args.realtime and not (args.date_from and args.date_to):
        parser.error('нужны --from и --to либо --realtime')

    print(
        build_document(
            build_client(),
            REPORTS,
            date_from=args.date_from,
            date_to=args.date_to,
            realtime=args.realtime,
        ),
        end='',
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
