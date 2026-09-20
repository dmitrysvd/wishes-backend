import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from scripts import ga4_export as ga

PROPERTY = '123456789'


def _ok(dims: list[str], metrics: list[str], rows: list[list[str]]) -> dict:
    return {
        'dimensionHeaders': [{'name': d} for d in dims],
        'metricHeaders': [{'name': m, 'type': 'TYPE_INTEGER'} for m in metrics],
        'rows': [
            {
                'dimensionValues': [{'value': v} for v in row[: len(dims)]],
                'metricValues': [{'value': v} for v in row[len(dims) :]],
            }
            for row in rows
        ],
        'rowCount': len(rows),
    }


class FakeApi:
    """Отвечает по имени метода (`runReport`/`runRealtimeReport`); запоминает
    тела запросов, чтобы проверить, что ушло в GA4."""

    def __init__(self, responses: dict[str, list[tuple[int, dict]]]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit(':', 1)[1]
        body = json.loads(request.content)
        self.requests.append((method, body))
        assert request.headers['Authorization'] == 'Bearer tok'
        status, payload = self.responses[method].pop(0)
        return httpx.Response(status, json=payload)


def _client(api: FakeApi) -> ga.GA4Client:
    return ga.GA4Client(
        PROPERTY, 'tok', httpx.Client(transport=httpx.MockTransport(api))
    )


def test_report_body_filters_event_and_drops_non_realtime_metrics():
    report = ga.REPORTS[3]  # screen_view
    body = report.body(realtime=False)
    assert body['dimensionFilter']['filter'] == {
        'fieldName': 'eventName',
        'stringFilter': {'value': 'screen_view'},
    }
    assert [m['name'] for m in body['metrics']] == ['eventCount', 'totalUsers']
    # totalUsers в Realtime API нет — остаётся только eventCount, по нему и сортировка.
    realtime = report.body(realtime=True)
    assert [m['name'] for m in realtime['metrics']] == ['eventCount']
    assert realtime['orderBys'] == [
        {'metric': {'metricName': 'eventCount'}, 'desc': True}
    ]
    assert 'dimensionFilter' not in ga.REPORTS[0].body(realtime=False)


def test_parse_report_handles_missing_rows():
    table = ga.parse_report({'dimensionHeaders': [{'name': 'platform'}]})
    assert table.headers == ('platform',) and table.rows == () and table.row_count == 0
    table = ga.parse_report(_ok(['platform'], ['activeUsers'], [['web', '5']]))
    assert table.rows == (('web', '5'),) and table.row_count == 1


def test_render_table_escapes_pipes_and_notes_truncation():
    assert ga.render_table(ga.Table(('a',), ())) == '_нет данных_'
    table = ga.Table(('screen', 'n'), (('a|b', '1'), ('', '2')), row_count=5)
    rendered = ga.render_table(table)
    assert rendered.splitlines()[:4] == [
        '| screen | n |',
        '|---|---|',
        '| a\\|b | 1 |',
        '| (not set) | 2 |',
    ]
    assert rendered.endswith('_показано 2 из 5 строк_')


def test_run_report_adds_date_range_and_raises_api_error():
    api = FakeApi(
        {
            'runReport': [
                (200, _ok(['platform'], ['activeUsers'], [['Android', '40']])),
                (
                    400,
                    {
                        'error': {
                            'status': 'INVALID_ARGUMENT',
                            'message': 'Field customEvent:method is not valid.',
                        }
                    },
                ),
            ]
        }
    )
    client = _client(api)
    table = client.run_report(ga.REPORTS[0], date(2026, 9, 1), date(2026, 9, 19))
    assert table.rows == (('Android', '40'),)
    assert api.requests[0][1]['dateRanges'] == [
        {'startDate': '2026-09-01', 'endDate': '2026-09-19'}
    ]
    with pytest.raises(ga.GA4Error, match='INVALID_ARGUMENT: Field customEvent'):
        client.run_report(ga.REPORTS[2], date(2026, 9, 1), date(2026, 9, 19))


def test_build_document_keeps_going_after_failed_report():
    api = FakeApi(
        {
            'runReport': [
                (200, _ok(['platform'], ['activeUsers'], [['Android', '40']])),
                (200, _ok(['eventName', 'platform'], ['eventCount'], [])),
                (403, {'error': {'status': 'PERMISSION_DENIED', 'message': 'no'}}),
                (
                    200,
                    _ok(
                        ['unifiedScreenName', 'platform'],
                        ['eventCount'],
                        [['home', 'web', '3']],
                    ),
                ),
            ]
        }
    )
    doc = ga.build_document(
        _client(api), ga.REPORTS, date_from=date(2026, 9, 1), date_to=date(2026, 9, 2)
    )
    assert doc.startswith(f'## GA4 properties/{PROPERTY}: 2026-09-01 — 2026-09-02')
    assert '| Android | 40 |' in doc
    assert '_нет данных_' in doc
    assert '⚠️ PERMISSION_DENIED: no' in doc
    assert '| home | web | 3 |' in doc
    assert len(api.requests) == 4
    with pytest.raises(ValueError, match='date_from'):
        ga.build_document(_client(api), ())


def test_build_document_realtime_uses_realtime_endpoint():
    api = FakeApi(
        {
            'runRealtimeReport': [
                (200, _ok(['platform'], ['activeUsers'], [['web', '1']]))
            ]
        }
    )
    doc = ga.build_document(_client(api), ga.REPORTS[:1], realtime=True)
    assert 'последние 30 минут (Realtime)' in doc and '| web | 1 |' in doc
    assert api.requests[0][0] == 'runRealtimeReport'
    assert 'dateRanges' not in api.requests[0][1]


def test_build_client_requires_property_and_key(mocker, tmp_path: Path):
    mocker.patch.object(ga.settings, 'GA4_PROPERTY_ID', None)
    with pytest.raises(SystemExit, match='GA4_PROPERTY_ID'):
        ga.build_client()
    mocker.patch.object(ga.settings, 'GA4_PROPERTY_ID', PROPERTY)
    mocker.patch.object(ga.settings, 'GA4_KEY_PATH', None)
    mocker.patch.object(ga.settings, 'FIREBASE_KEY_PATH', tmp_path / 'missing.json')
    with pytest.raises(SystemExit, match='Нет ключа'):
        ga.build_client()
    key = tmp_path / 'ga4.json'
    key.write_text('{}')
    mocker.patch.object(ga.settings, 'GA4_KEY_PATH', key)
    token = mocker.patch('scripts.ga4_export.service_account_token', return_value='tok')
    client = ga.build_client(httpx.Client())
    assert client.property == f'properties/{PROPERTY}'
    token.assert_called_once_with(key)


def test_main_requires_period_or_realtime(mocker, capsys):
    with pytest.raises(SystemExit):
        ga.main([])
    assert '--from и --to либо --realtime' in capsys.readouterr().err
    mocker.patch('scripts.ga4_export.build_client', return_value=object())
    build = mocker.patch('scripts.ga4_export.build_document', return_value='doc\n')
    assert ga.main(['--realtime']) == 0
    assert build.call_args.kwargs['realtime'] is True
    assert capsys.readouterr().out == 'doc\n'
    assert ga.main(['--from', '2026-09-01', '--to', '2026-09-02']) == 0
    assert build.call_args.kwargs['date_from'] == date(2026, 9, 1)
