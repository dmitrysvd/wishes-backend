import gzip
from datetime import UTC, date, datetime
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import text

from scripts import mytracker_export as mt

API_ID = '77658'
SECRET = 'secret'


def test_auth_header_matches_docs_example():
    # Пример из документации myTracker: подпись GET без тела.
    url = 'https://tracker.my.com/api/raw/v1/export/get.json?idReport=4'
    header = mt.auth_header(API_ID, 'ihcAJl9fiDW9Vr3xVYsy5ppPY5XVcMiK', 'get', url)
    assert header.startswith('AuthHMAC 77658:')
    # Тело POST входит в подпись — другой body, другая подпись.
    a = mt.auth_header(API_ID, SECRET, 'POST', url, 'event=installs')
    b = mt.auth_header(API_ID, SECRET, 'POST', url, 'event=sessions')
    assert a != b


def test_parse_create_and_status_responses():
    assert mt.parse_create_response({'code': 200, 'data': {'idRawExport': '2'}}) == 2
    with pytest.raises(RuntimeError, match='Maximum number'):
        mt.parse_create_response(
            {'code': 400, 'message': 'Maximum number of simultaneous requests reached'}
        )
    status = mt.parse_status_response(
        {
            'code': 200,
            'data': {
                'idRawExport': '4',
                'status': 'Success!',
                'files': [
                    {'link': 'https://x/0.csv.gz'},
                    {'link': 'https://x/1.csv.gz'},
                ],
            },
        }
    )
    assert status.done and status.files == ('https://x/0.csv.gz', 'https://x/1.csv.gz')
    progress = mt.parse_status_response(
        {'code': 200, 'data': {'status': 'In progress', 'progress': '64%'}}
    )
    assert not progress.done and not progress.failed and progress.progress == '64%'
    failed = mt.parse_status_response(
        {
            'code': 200,
            'data': {'status': 'User error occurred', 'errorMessage': 'Too many lines'},
        }
    )
    assert failed.failed and failed.error == 'Too many lines'
    with pytest.raises(RuntimeError, match='Not Found'):
        mt.parse_status_response({'code': 404, 'message': 'Not Found'})


def test_quota_reset_in():
    payload = {
        'data': {'error': {'info': {'quotas': [{'resetIn': 7}, {'resetIn': 30}]}}}
    }
    assert mt._quota_reset_in(payload) == 31
    assert mt._quota_reset_in({}) == 61


def _csv_gz(header: list[str], rows: list[list[str]]) -> bytes:
    lines = [','.join(header)] + [','.join(r) for r in rows]
    return gzip.compress(('\n'.join(lines) + '\n').encode())


USER_ID = '7c9e6679-7425-40de-944b-e07fc1f90ae7'


def test_rows_for_table_maps_selectors_and_skips_keyless():
    records = mt.read_csv_gz(
        _csv_gz(
            [
                'idDevice',
                'idProfile',
                'customUserId',
                'tsEvent',
                'idAppVersionTitle',
                'idOsVersionTitle',
                'eventName',
                'eventValue',
            ],
            [
                ['d1', 'p1', USER_ID, '1758196800', '1.1.16', '14', 'LOGIN', ''],
                [
                    'd1',
                    'p1',
                    'not-a-uuid',
                    '1758196801',
                    '1.1.16',
                    '14',
                    'gms_available',
                    '1',
                ],
                ['d2', 'p2', '', '', '1.1.15', '13', 'LOGIN', ''],
            ],
        )
    )
    rows = mt.rows_for_table(mt.KINDS['events'], records)
    assert len(rows) == 2  # третья — без tsEvent
    assert rows[0]['event_at'] == datetime(2025, 9, 18, 12, 0, tzinfo=UTC)
    assert str(rows[0]['user_id']) == USER_ID
    assert rows[0]['event_name'] == 'LOGIN' and rows[0]['event_value'] is None
    # Чужой формат customUserId сохраняется как текст, но в uuid не парсится.
    assert rows[1]['custom_user_id'] == 'not-a-uuid' and rows[1]['user_id'] is None


def test_rows_for_installs_and_sessions():
    installs = mt.rows_for_table(
        mt.KINDS['installs'],
        [
            {
                'idProfile': 'p1',
                'tsEvent': '10',
                'idCountryISOAlpha2': 'RU',
            }
        ],
    )
    assert installs[0]['installed_at'] == datetime.fromtimestamp(10, tz=UTC)
    assert installs[0]['country'] == 'RU' and installs[0]['device_model'] is None
    sessions = mt.rows_for_table(
        mt.KINDS['sessions'], [{'idDevice': 'd', 'tsEvent': '10', 'duration': '42'}]
    )
    # Без idProfile ключом становится idDevice.
    assert sessions[0]['id_profile'] == 'd' and sessions[0]['duration'] == 42


class FakeApi:
    """Сервер myTracker в памяти: create → id, get → статусы по очереди, файл."""

    def __init__(self, statuses: list[dict], files: dict[str, bytes]):
        self.statuses = statuses
        self.files = files
        self.created: list[dict[str, list[str]]] = []
        self.quota_hits = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers['Authorization'].startswith(f'AuthHMAC {API_ID}:')
        url = str(request.url)
        if url.endswith('/create.json'):
            self.created.append(parse_qs(request.content.decode()))
            return httpx.Response(200, json={'code': 200, 'data': {'idRawExport': '4'}})
        if '/get.json' in url:
            data = self.statuses.pop(0)
            if data.get('quota'):
                self.quota_hits += 1
                return httpx.Response(429, json={'code': 429, 'data': {'error': {}}})
            return httpx.Response(200, json={'code': 200, 'data': data})
        return httpx.Response(200, content=self.files[url])


@pytest.fixture
def conn(test_engine):
    with test_engine.connect() as connection:
        yield connection
        connection.rollback()
        connection.execute(text(f'DROP SCHEMA IF EXISTS {mt.SCHEMA} CASCADE'))
        connection.commit()


def _client(api: FakeApi) -> mt.MyTrackerClient:
    return mt.MyTrackerClient(
        API_ID, SECRET, httpx.Client(transport=httpx.MockTransport(api))
    )


def test_run_export_end_to_end_is_idempotent(conn, mocker):
    mocker.patch('scripts.mytracker_export.time.sleep')
    file = _csv_gz(
        ['idProfile', 'customUserId', 'tsEvent', 'duration', 'idAppVersionTitle'],
        [
            ['p1', USER_ID, '100', '30', '1.1.16'],
            ['p1', USER_ID, '200', '10', '1.1.16'],
        ],
    )
    api = FakeApi(
        statuses=[
            {'status': 'In progress', 'progress': '10%'},
            {'quota': True},
            {'status': 'Success!', 'files': [{'link': 'https://f/0.csv.gz'}]},
            # Второй запуск (--force) — сразу успех.
            {'status': 'Success!', 'files': [{'link': 'https://f/0.csv.gz'}]},
        ],
        files={'https://f/0.csv.gz': file},
    )
    kind = mt.KINDS['sessions']
    window = (date(2026, 9, 1), date(2026, 9, 19))

    assert mt.run_export(_client(api), conn, kind, *window, poll_interval=0) == 2
    assert api.quota_hits == 1
    (params,) = api.created
    assert params['event'] == ['sessions'] and params['timezone'] == ['UTC']
    assert params['selectors'] == [','.join(kind.selectors)]

    # Тот же период без --force: новый запрос не создаётся, ничего не вставляется.
    assert mt.run_export(_client(api), conn, kind, *window) == 0
    assert len(api.created) == 1
    # С --force: запрос создаётся, но строки по ключу уже есть — 0 вставок.
    assert mt.run_export(_client(api), conn, kind, *window, force=True) == 0
    assert len(api.created) == 2
    assert (
        conn.execute(text(f'SELECT count(*) FROM {mt.SCHEMA}.sessions')).scalar() == 2
    )
    statuses = (
        conn.execute(
            text(f'SELECT status FROM {mt.SCHEMA}.export_request ORDER BY created_at')
        )
        .scalars()
        .all()
    )
    assert statuses == ['loaded']  # оба запроса имели id 4 → одна строка, upsert


def test_run_export_resumes_pending_and_records_failure(conn, mocker):
    mocker.patch('scripts.mytracker_export.time.sleep')
    kind = mt.KINDS['installs']
    window = (date(2026, 9, 1), date(2026, 9, 2))
    mt.ensure_schema(conn)
    mt.save_request(conn, 9, kind, *window, status='pending')
    conn.commit()
    api = FakeApi(
        statuses=[{'status': 'User error occurred', 'errorMessage': 'Too many lines'}],
        files={},
    )
    with pytest.raises(RuntimeError, match='Too many lines'):
        mt.run_export(_client(api), conn, kind, *window)
    # Незавершённый запрос переиспользован (create не звался) и помечен failed.
    assert api.created == []
    assert mt.find_request(conn, kind, *window) == (9, 'failed')


def test_build_client_requires_keys(mocker):
    mocker.patch.object(mt.settings, 'MYTRACKER_API_USER_ID', None)
    with pytest.raises(SystemExit, match='MYTRACKER_API_USER_ID'):
        mt.build_client()
    mocker.patch.object(mt.settings, 'MYTRACKER_API_USER_ID', API_ID)
    mocker.patch.object(mt.settings, 'MYTRACKER_API_SECRET', SECRET)
    assert isinstance(mt.build_client(httpx.Client()), mt.MyTrackerClient)


def test_main_runs_each_kind(mocker, test_engine, capsys):
    mocker.patch.object(mt.settings, 'MYTRACKER_API_USER_ID', API_ID)
    mocker.patch.object(mt.settings, 'MYTRACKER_API_SECRET', SECRET)
    mocker.patch('scripts.mytracker_export.create_engine', return_value=test_engine)
    run = mocker.patch('scripts.mytracker_export.run_export', return_value=3)
    assert (
        mt.main(
            [
                '--kind',
                'sessions',
                '--kind',
                'events',
                '--from',
                '2026-09-01',
                '--to',
                '2026-09-02',
            ]
        )
        == 0
    )
    assert [c.args[2].name for c in run.call_args_list] == ['sessions', 'events']
    assert capsys.readouterr().out == 'Вставлено строк: 6\n'
