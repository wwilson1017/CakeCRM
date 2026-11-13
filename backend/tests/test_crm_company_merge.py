"""Company merge contracts: preview purity, gated transaction, refusals, HTTP and tools."""
from contextlib import contextmanager
from unittest.mock import Mock

import psycopg2
import pytest
from conftest import fake_member
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth import get_current_user
from crm import company_merge_service as merge, scoring_service, service, tools
from crm.router import router


def company(cid, name, **changes):
    return {**dict.fromkeys(merge._COLUMNS, ''), 'id': cid, 'name': name,
            'status': 'active', 'merged_into_id': None, **changes}


class Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.counts = iter([2, 3, 1, 4, 5, 6])
        self.collision = None

    def execute(self, sql, params=()):
        self.sql = ' '.join(sql.split())
        self.params = params
        self.calls.append((self.sql, params))

    def fetchall(self):
        if self.sql.startswith('SELECT id, name, status'):
            return [tuple(row[k] for k in merge._COLUMNS) for row in self.rows]
        if self.sql.startswith('UPDATE contacts'):
            return [(10,), (11,)]
        if self.sql.startswith('UPDATE deals'):
            return [(20,), (21,), (22,)]
        raise AssertionError(self.sql)

    def fetchone(self):
        if 'count(*)' in self.sql:
            return (next(self.counts),)
        if self.sql.startswith('SELECT LOWER'):
            return (self.params[0].strip().lower() == self.params[1].strip().lower(),)
        if self.sql.startswith('SELECT id FROM companies'):
            return self.collision
        raise AssertionError(self.sql)


@pytest.fixture
def db(monkeypatch):
    rows = [company(1, 'Kept', notes='keep'), company(2, 'Duplicate', notes='old', domain='dupe.example')]
    cursors = []
    connections = []
    exited = []

    @contextmanager
    def connection():
        cur = Cursor(rows)
        cursors.append(cur)
        conn = Mock()
        conn.cursor.return_value = cur
        connections.append(conn)
        yield conn
        exited.append(True)

    def after_commit(**kwargs):
        assert len(exited) == len(connections)
    score = Mock(side_effect=after_commit)
    monkeypatch.setattr(merge, 'get_connection', connection)
    monkeypatch.setattr(scoring_service, 'score_on_event', score)
    return rows, cursors, connections, score, exited


def test_preview_is_read_only_and_merge_recomputes_same_plan(db):
    _, cursors, conns, score, exited = db
    plan = merge.plan_merge(1, 2, rename_to='Final')
    assert plan['moves'] == dict(contacts=2, deals=3, archived_deals=1, todos=4, notes=5, custom_fields=6)
    assert plan['fields'] == [
        {'field': 'name', 'old': 'Kept', 'new': 'Final'},
        {'field': 'domain', 'old': '', 'new': 'dupe.example'},
        {'field': 'notes', 'old': 'keep', 'new': 'keep\n\nFrom Duplicate: old'},
    ]
    assert all(sql.startswith('SELECT') and 'FOR UPDATE' not in sql for sql, _ in cursors[0].calls)
    assert not any('company_merge_gate' in sql for sql, _ in cursors[0].calls)
    conns[0].rollback.assert_called_once()
    score.assert_not_called()
    assert merge.merge_companies(1, 2, reason='same org', actor_id=2, rename_to='Final') == plan
    calls = cursors[1].calls
    assert calls[0][0] == 'SELECT company_merge_gate()'
    assert 'WHERE id IN (%s, %s) ORDER BY id FOR UPDATE' in calls[1][0]
    assert calls[1][1] == (1, 2)
    assert exited == [True, True]
    score.assert_called_once_with(contact_ids=[10, 11], deal_ids=[20, 21, 22])


@pytest.mark.parametrize('rename', [None, '', ' kept '])
def test_blank_or_equivalent_rename_is_noop(db, rename):
    assert not any(f['field'] == 'name' for f in merge.plan_merge(1, 2, rename_to=rename)['fields'])


@pytest.mark.parametrize('case, error, message', [
    ('self', ValueError, 'itself'), ('missing', LookupError, 'not found'),
    ('archived', ValueError, 'restore it first'), ('survivor_merged', ValueError, 'instead'),
    ('absorbed_merged', ValueError, 'already merged'), ('collision', ValueError, 'already has'),
])
@pytest.mark.parametrize('preview', [False, True])
def test_refusals_before_any_write(db, monkeypatch, case, error, message, preview):
    rows, cursors, _, score, _ = db
    if case == 'missing':
        rows.pop()
    if case == 'archived':
        rows[0]['status'] = 'archived'
    if case == 'survivor_merged':
        rows[0]['merged_into_id'] = 3
    if case == 'absorbed_merged':
        rows[1]['merged_into_id'] = 3
    if case == 'collision':
        original = Cursor.fetchone
        monkeypatch.setattr(Cursor, 'fetchone', lambda self: (2,) if self.sql.startswith('SELECT id FROM companies') else original(self))
    args = (1, 1 if case == 'self' else 2)
    kwargs = {'rename_to': 'Duplicate' if case == 'collision' else None}
    if not preview:
        kwargs.update(reason='why', actor_id=2)
    with pytest.raises(error, match=message):
        (merge.plan_merge if preview else merge.merge_companies)(*args, **kwargs)
    assert all(sql.startswith('SELECT') for sql, _ in cursors[0].calls)
    score.assert_not_called()


def test_blank_reason_never_opens_connection(db):
    with pytest.raises(ValueError):
        merge.merge_companies(1, 2, actor_id=2, reason='  ')
    assert db[2] == []


def test_archived_absorbed_is_allowed(db):
    db[0][1]['status'] = 'archived'
    assert merge.merge_companies(1, 2, actor_id=2, reason='why')['absorbed']['status'] == 'archived'


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix='/api/crm')
    app.dependency_overrides[get_current_user] = fake_member
    with TestClient(app) as client:
        yield client


def test_http_contracts(client, monkeypatch):
    preview = Mock(return_value={'moves': {}})
    write = Mock(return_value={'moves': {}})
    monkeypatch.setattr(merge, 'plan_merge', preview)
    monkeypatch.setattr(merge, 'merge_companies', write)
    assert client.get('/api/crm/companies/2/merge-preview?survivor_id=1&rename_to=Final').status_code == 200
    preview.assert_called_once_with(1, 2, rename_to='Final')
    assert client.post('/api/crm/companies/2/merge', json={'survivor_id': 1, 'reason': 'why'}).status_code == 200
    write.assert_called_once_with(1, 2, actor_id=2, reason='why', rename_to=None)
    assert client.post('/api/crm/companies/2/merge', json={}).status_code == 422
    assert client.post('/api/crm/companies/2/merge', json={'survivor_id': True, 'reason': 'why'}).status_code == 422


@pytest.mark.parametrize('error, status', [(LookupError('missing'), 404), (ValueError('invalid'), 400),
                                          (psycopg2.errors.UniqueViolation(), 409)])
def test_http_errors(client, monkeypatch, error, status):
    monkeypatch.setattr(merge, 'merge_companies', Mock(side_effect=error))
    assert client.post('/api/crm/companies/2/merge', json={'survivor_id': 1, 'reason': 'why'}).status_code == status
    if status != 409:
        monkeypatch.setattr(merge, 'plan_merge', Mock(side_effect=error))
        assert client.get('/api/crm/companies/2/merge-preview?survivor_id=1').status_code == status


def test_http_blank_reason(client, db):
    assert client.post('/api/crm/companies/2/merge', json={'survivor_id': 1, 'reason': ' '}).status_code == 400
    assert db[2] == []


def test_tool_success_identity_and_read_definition(monkeypatch):
    write = Mock(return_value={'moves': {}})
    monkeypatch.setattr(merge, 'merge_companies', write)
    monkeypatch.setattr(tools, 'pg_fetchall', lambda *args: [{'id': 1, 'name': 'Kept', 'owner_id': None}])
    result = tools._identity_executors(fake_member())['crm_merge_companies'](survivor_id=1, absorbed_id=2, reason='why', actor_id=99)
    write.assert_called_once_with(1, 2, actor_id=2, reason='why', rename_to=None)
    assert result['ok'] and result['merged_from'] == 2 and result['plan'] == {'moves': {}}
    assert result['target']['id'] == 1
    definition = next(d for d in tools.CRM_TOOL_DEFS if d['name'] == 'crm_preview_company_merge')
    assert definition['writes'] is False


@pytest.mark.parametrize('error', [ValueError('invalid'), LookupError('missing'), psycopg2.errors.UniqueViolation()])
def test_tool_errors(monkeypatch, error):
    monkeypatch.setattr(merge, 'merge_companies', Mock(side_effect=error))
    expected = 'Another company already has that name' if isinstance(error, psycopg2.errors.UniqueViolation) else str(error)
    assert tools.crm_merge_companies(1, 2, reason='why') == {'error': expected}
    if not isinstance(error, psycopg2.errors.UniqueViolation):
        monkeypatch.setattr(merge, 'plan_merge', Mock(side_effect=error))
        assert tools.crm_preview_company_merge(1, 2) == {'error': expected}


def test_assignment_gate_precedes_contact_lock(monkeypatch):
    cur = Mock()
    cur.fetchone.side_effect = [(3,), ('active',)]
    conn = Mock()
    conn.cursor.return_value = cur
    @contextmanager
    def connection():
        yield conn
    monkeypatch.setattr(service, 'get_connection', connection)
    assert service._write_status_audited('contacts', 'contact', 4, {'company_id': 2}, None, None)
    assert cur.execute.call_args_list[0].args == ('SELECT company_follow_merge(%s)', (2,))
    assert cur.execute.call_args_list[-1].args[1][0] == 3


def test_gate_precedes_deal_lock_and_redirects(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[(3,), ('lead', None, None, None)])
    monkeypatch.setattr(scoring_service, 'score_on_event', Mock())
    assert service._write_deal_update(4, {'company_id': 2})
    assert conn.executed[0] == ('SELECT company_follow_merge(%s)', (2,))
    assert 'FOR UPDATE' in conn.executed[1][0]
    params = next(p for sql, p in conn.executed if sql.startswith('UPDATE deals'))
    assert params[0] == 3


def test_delete_takes_exclusive_gate_first(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[None])
    assert service.delete_company(1) is False
    assert conn.executed[0][0] == 'SELECT company_merge_gate()'
    assert 'FOR UPDATE' in conn.executed[1][0]


def test_restore_refusal_under_the_company_lock(monkeypatch, fake_conn):
    conn = fake_conn(monkeypatch, service, fetchone_results=[('archived',), (3,)])
    with pytest.raises(ValueError, match='cannot be restored'):
        service.update_company(1, status='active')
    assert 'FOR UPDATE' in conn.executed[0][0]
    assert all(sql.startswith('SELECT') for sql, _ in conn.executed)


def test_gap_fill_preserves_populated_values_and_identical_notes(db):
    survivor, absorbed = db[0]
    survivor.update(domain='kept.example', industry='  ', notes=' same ')
    absorbed.update(domain='dupe.example', industry='Retail', phone='123', address='Road', source='Import', notes='same')
    fields = merge.plan_merge(1, 2)['fields']
    assert {f['field'] for f in fields} == {'industry', 'phone', 'address', 'source'}
    assert next(f for f in fields if f['field'] == 'industry')['new'] == 'Retail'


def test_merge_copies_the_marker_and_marks_its_automatic_notes(db):
    _, cursors, _, _, _ = db
    merge.merge_companies(1, 2, reason="same org", actor_id=2)
    inserts = [sql for sql, _ in cursors[0].calls if "INSERT INTO crm_chatter" in sql]
    copy = next(sql for sql in inserts if "left(%s || message, %s)" in sql)
    assert copy.count("archived, system_note") == 2
    notes = [sql for sql in inserts if sql != copy]
    assert len(notes) == 2
    assert all("system_note" in sql and sql.endswith("true)") for sql in notes)
