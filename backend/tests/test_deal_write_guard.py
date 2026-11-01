"""Dispatch-level deal find discipline, with only the database and tool sources faked."""

import pytest

from assistant import registry as registry_module
from crm import deal_write_guard as guard
from crm.links import with_deal_url


@pytest.fixture
def guard_db(monkeypatch):
    finds = {}  # (conversation_id, deal_id) -> crm epoch
    closed = []
    crm = {"epoch": 1}

    def fetch(sql, params):
        if 'assistant_deal_finds' in sql:
            return [{"deal_id": i} for (cid, i), epoch in sorted(finds.items())
                    if cid == params[0] and epoch == params[1]]
        return [row for row in closed if row['id'] in params[0]]

    def execute(sql, params):
        assert 'ON CONFLICT' in sql
        finds.update(((params[0], i), params[2]) for i in params[1])

    monkeypatch.setattr(guard, 'pg_fetchall', fetch)
    monkeypatch.setattr(guard, 'pg_execute', execute)
    monkeypatch.setattr(guard, 'pg_fetchone', lambda sql: {"updated_at": crm["epoch"]})
    return finds, closed, crm


@pytest.fixture
def make_registry(monkeypatch, guard_db):
    row = with_deal_url({"id": 7, "title": "Example renewal"})
    names = {"crm_get_deal": False, "crm_update_deal": True, "crm_archive_deal": True,
             "crm_create_deal": True, "help_read": False}
    defs = [{"name": name, "writes": write, "description": "test"} for name, write in names.items()]
    for source in ('get_gtd_tools', 'get_reminder_tools', 'get_memory_tools',
                   'get_context_file_tools', 'get_gmail_tools', 'get_help_tools'):
        monkeypatch.setattr(registry_module, source, lambda **kw: ([], {}))

    def make(cid=None):
        calls = []

        def execute(**args):
            calls.append(args)
            return dict(row)

        monkeypatch.setattr(registry_module, 'get_crm_tools', lambda **kw: (
            defs, {name: execute for name in names}))
        registry = registry_module.ToolRegistry()
        registry.conversation_id = cid
        registry.calls = calls
        return registry

    return make


def test_unfound_and_same_batch_refused_then_next_batch_runs(make_registry):
    reg = make_registry()
    assert reg.execute_tool_sync('crm_update_deal', {'deal_id': 7})['ok'] is False
    assert reg.calls == []
    reg.next_tool_batch()
    reg.execute_tool_sync('crm_get_deal', {'deal_id': 7})
    assert 'same batch' in reg.execute_tool_sync('crm_update_deal', {'deal_id': 7})['error']
    assert len(reg.calls) == 1
    reg.next_tool_batch()
    assert reg.execute_tool_sync('crm_update_deal', {'deal_id': 7})['id'] == 7
    assert len(reg.calls) == 2


@pytest.mark.parametrize('name,counts', [('crm_update_deal', False), ('help_read', False),
                                        ('crm_create_deal', True), ('crm_get_deal', True)])
def test_only_crm_reads_and_creation_find(make_registry, name, counts):
    reg = make_registry()
    # A write echo can carry a deal-shaped target even when it named no deal slot.
    reg.execute_tool_sync(name, {})
    reg.next_tool_batch()
    result = reg.execute_tool_sync('crm_update_deal', {'deal_id': 7})
    assert ('error' not in result) is counts


@pytest.mark.parametrize('args', [{'deal_ids': [7, 8]}, {'source_deal_id': 7, 'target_deal_id': 8},
                                 {'entity_type': 'deal', 'entity_id': 8}, {'deal_id': 8}])
def test_all_deal_targets_require_finds(make_registry, args):
    reg = make_registry()
    reg.execute_tool_sync('crm_get_deal', {})
    reg.next_tool_batch()
    assert '8' in reg.execute_tool_sync('crm_update_deal', args)['error']
    assert len(reg.calls) == 1


@pytest.mark.parametrize('value', ['7', True, 7.0, {}, [7]])
@pytest.mark.parametrize('slot', ['deal_id', 'source_deal_id', 'target_deal_id', 'entity_id', 'deal_ids'])
def test_malformed_ids_never_reach_handler(make_registry, slot, value):
    reg = make_registry()
    args = {slot: [value] if slot == 'deal_ids' else value}
    if slot == 'entity_id':
        args['entity_type'] = 'deal'
    assert 'integers' in reg.execute_tool_sync('crm_update_deal', args)['error']
    assert reg.calls == []


@pytest.mark.parametrize('value', [None, '7', 7, True, {}])
def test_deal_ids_requires_list(make_registry, value):
    reg = make_registry()
    assert 'list' in reg.execute_tool_sync('crm_update_deal', {'deal_ids': value})['error']
    assert reg.calls == []


def test_optional_links_and_contacts_are_not_deal_targets(make_registry):
    reg = make_registry()
    for args in ({'deal_id': None}, {'deal_ids': [None]}, {'entity_type': 'contact', 'entity_id': '7'}):
        assert 'error' not in reg.execute_tool_sync('crm_update_deal', args)
    assert len(reg.calls) == 3


def test_next_message_and_conversation_isolation(make_registry, guard_db):
    first = make_registry('chat')
    first.execute_tool_sync('crm_get_deal', {})
    assert set(guard_db[0]) == {('chat', 7)}
    second = make_registry('chat')
    assert 'error' not in second.execute_tool_sync('crm_update_deal', {'deal_id': 7})
    assert 'error' in make_registry('other').execute_tool_sync('crm_update_deal', {'deal_id': 7})


@pytest.mark.parametrize('stage,archived,label', [('lost', None, 'lost'), ('lead', 'date', 'archived')])
def test_closed_warning_only_on_success(make_registry, guard_db, stage, archived, label):
    reg = make_registry()
    reg.execute_tool_sync('crm_get_deal', {})
    reg.next_tool_batch()
    guard_db[1].append({'id': 7, 'title': 'Example renewal', 'stage': stage, 'archived_at': archived})
    result = reg.execute_tool_sync('crm_update_deal', {'deal_id': 7})
    assert label in result['closed_deal_warning']
    for failed in ({'ok': False}, {'error': 'not found'}):
        reg.executors['crm_update_deal'] = lambda **kw: dict(failed)
        assert 'closed_deal_warning' not in reg.execute_tool_sync('crm_update_deal', {'deal_id': 7})


def test_database_failure_fails_closed_but_local_reads_still_work(make_registry, monkeypatch):
    def broken(*args):
        raise RuntimeError('unavailable')

    monkeypatch.setattr(guard, 'pg_fetchall', broken)
    monkeypatch.setattr(guard, 'pg_execute', broken)
    reg = make_registry('chat')
    assert 'error' in reg.execute_tool_sync('crm_update_deal', {'deal_id': 7})
    reg.execute_tool_sync('crm_get_deal', {})
    reg.next_tool_batch()
    assert 'error' not in reg.execute_tool_sync('crm_update_deal', {'deal_id': 7})
    assert 'error' in make_registry('chat').execute_tool_sync('crm_update_deal', {'deal_id': 7})


@pytest.mark.parametrize('found', [False, True])
def test_confirmation_rechecks_finds_and_warns(make_registry, guard_db, monkeypatch, found):
    from assistant import engine, history

    if found:
        make_registry('chat').execute_tool_sync('crm_get_deal', {})
    guard_db[1].append({'id': 7, 'title': 'Example renewal', 'stage': 'lost', 'archived_at': None})
    monkeypatch.setattr(history, 'claim_pending_tool', lambda *a, **kw: {
        'msg_id': 'm', 'tool': 'crm_archive_deal', 'args': {'deal_id': 7}})
    monkeypatch.setattr(history, 'merge_tool_result', lambda *a, **kw: None)
    reg = make_registry()
    result = engine.resolve_confirmation(reg, 'chat', 't', 'approve', user=None)['result']
    if found:
        assert 'lost' in result['closed_deal_warning']
        assert len(reg.calls) == 1
    else:
        assert result['ok'] is False
        assert reg.calls == []


async def test_background_finds_cross_batches_but_not_runs(make_registry):
    from test_assistant_engine import FakeProvider, _complete, _tc

    from assistant.background import _run_turn

    reg = make_registry()
    provider = FakeProvider([
        [_complete([_tc('crm_get_deal'), _tc('crm_update_deal', 'w0', {'deal_id': 7})], 'tool_use')],
        [_complete([_tc('crm_update_deal', 'w1', {'deal_id': 7})], 'tool_use')],
        [_complete()],
    ])
    allowed = {'crm_get_deal', 'crm_update_deal'}
    await _run_turn(provider, reg, 'sys', 'update', allowed, 5, 10)
    assert len(reg.calls) == 2
    assert reg.tool_batch == 2
    other = make_registry()
    await _run_turn(FakeProvider([
        [_complete([_tc('crm_update_deal', args={'deal_id': 7})], 'tool_use')], [_complete()]
    ]), other, 'sys', 'update', allowed, 5, 10)
    assert other.calls == []


def test_unexpected_guard_failure_refuses_before_handler(make_registry, monkeypatch):
    def broken(*args):
        raise RuntimeError("unexpected guard failure")

    monkeypatch.setattr(guard, "refusal", broken)
    reg = make_registry()
    assert reg.execute_tool_sync("crm_update_deal", {"deal_id": 7})["ok"] is False
    assert reg.calls == []


def test_repeat_read_does_not_age_a_prior_find_into_current_batch(make_registry):
    reg = make_registry()
    reg.execute_tool_sync("crm_get_deal", {})
    reg.next_tool_batch()
    reg.execute_tool_sync("crm_get_deal", {})
    assert "error" not in reg.execute_tool_sync("crm_update_deal", {"deal_id": 7})


def test_reset_invalidates_a_pending_approval(make_registry, guard_db, monkeypatch):
    from assistant import engine, history

    reg = make_registry("chat")
    reg.execute_tool_sync("crm_get_deal", {})
    reg.next_tool_batch()
    assert reg.deal_write_refusal("crm_archive_deal", {"deal_id": 7}) is None
    guard_db[0].clear()  # CRM reset between card creation and approval.
    monkeypatch.setattr(history, "claim_pending_tool", lambda *a, **kw: {
        "msg_id": "m", "tool": "crm_archive_deal", "args": {"deal_id": 7}})
    monkeypatch.setattr(history, "merge_tool_result", lambda *a, **kw: None)
    fresh = make_registry()
    result = engine.resolve_confirmation(fresh, "chat", "t", "approve", user=None)["result"]
    assert result["ok"] is False
    assert fresh.calls == []


def test_reset_mid_turn_drops_the_cached_find(make_registry, guard_db):
    reg = make_registry("chat")
    reg.execute_tool_sync("crm_get_deal", {})
    reg.next_tool_batch()
    guard_db[0].clear()
    guard_db[2]["epoch"] = 2  # CRM reset; a new deal 7 may now exist.
    assert reg.execute_tool_sync("crm_update_deal", {"deal_id": 7})["ok"] is False
    assert len(reg.calls) == 1


def test_find_saved_after_a_racing_reset_does_not_count(make_registry, guard_db):
    reg = make_registry("chat")
    read = reg.executors["crm_get_deal"]

    def read_then_reset(**args):
        result = read(**args)
        guard_db[2]["epoch"] = 2  # the reset lands after the read, before its find is saved
        return result

    reg.executors["crm_get_deal"] = read_then_reset
    reg.execute_tool_sync("crm_get_deal", {})
    assert guard_db[0] == {("chat", 7): 1}
    reg.next_tool_batch()
    assert reg.execute_tool_sync("crm_update_deal", {"deal_id": 7})["ok"] is False
    assert make_registry("chat").execute_tool_sync("crm_update_deal", {"deal_id": 7})["ok"] is False


def test_unreadable_epoch_refuses_writes_and_records_no_find(make_registry, guard_db, monkeypatch):
    def broken(sql):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(guard, "pg_fetchone", broken)
    reg = make_registry("chat")
    reg.execute_tool_sync("crm_get_deal", {})
    assert guard_db[0] == {}
    reg.next_tool_batch()
    assert reg.execute_tool_sync("crm_update_deal", {"deal_id": 7})["ok"] is False
