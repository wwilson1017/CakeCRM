"""Real Postgres company merge, lifecycle and advisory-gate races (#334)."""
import os
import threading
import time
from contextlib import contextmanager

import psycopg2
import pytest

from core import postgres
from core.postgres import pg_execute, pg_fetchall, pg_fetchone
from crm import chatter_service, company_merge_service as merge, field_service, service

pytestmark = pytest.mark.integration
ADMIN_DSN = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")

@pytest.fixture(scope="module")
def pg_db():
    from core import postgres

    dbname = f"cakecrm_it_cmerge_{os.getpid()}"
    admin = psycopg2.connect(ADMIN_DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        cur.execute(f'CREATE DATABASE "{dbname}"')
    admin.close()

    dsn = ADMIN_DSN.rsplit("/", 1)[0] + f"/{dbname}"
    prev = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = dsn
    postgres.close_pool()
    postgres.init_pool()
    # A FRESH database matters: the migration runner records applied filenames, so
    # re-applying this new migration against a dirty one would silently skip it.
    postgres.run_migrations()
    yield dsn

    postgres.close_pool()
    if prev is not None:
        os.environ["DATABASE_URL"] = prev
    else:
        os.environ.pop("DATABASE_URL", None)
    admin = psycopg2.connect(ADMIN_DSN)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (dbname,),
        )
        cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
    admin.close()


@pytest.fixture(autouse=True)
def _clean(pg_db):
    from core.postgres import get_connection
    from crm import service

    with get_connection() as conn:
        service._truncate_all(conn.cursor(), include_definitions=True)
    yield



def companies():
    return [service.create_company(name)["id"] for name in ("First Org", "Second Org", "Third Org")]


def merge_into(s, a):
    return merge.merge_companies(s, a, actor_id=None, reason="same org")


def test_merge_moves_fills_and_audits(pg_db):
    s = service.create_company("Kept Org", notes="kept")["id"]
    a = service.create_company("Duplicate Org", domain="dupe.example", notes="old")["id"]
    pg_execute("INSERT INTO users (id, email, name, password_hash, role) VALUES (1, 'admin@example.test', 'Admin', '!', 'admin') ON CONFLICT (id) DO NOTHING")
    contacts = [service.create_contact(name, company_id=a)["id"] for name in ("Robin", "Taylor")]
    deals = [service.create_deal(name, company_id=a)["id"] for name in ("Current", "Old")]
    service.archive_deal(deals[1], reason="old")
    service.create_todo("Call", contact_id=contacts[0])
    service.create_todo("Old followup", deal_id=deals[1])
    chatter_service.add_note("company", a, "old")
    fields = [field_service.create_field_definition(dict(entity_type="company", field_type="text", name=name))["id"]
              for name in ("Region", "Tier")]
    field_service.set_field_values("company", a, {str(fields[0]): "west", str(fields[1]): "source"}, "admin@example.test")
    field_service.set_field_values("company", s, {str(fields[0]): "", str(fields[1]): "kept"}, "admin@example.test")
    before_contacts = [service.get_contact(cid)['updated_at'] for cid in contacts]
    before_deals = [service.get_deal(did)['updated_at'] for did in deals]
    activity = service.log_activity('call', 'Discussed next steps', contact_id=contacts[0], deal_id=deals[0])
    plan = merge.plan_merge(s, a)
    assert plan['moves'] == dict(contacts=2, deals=2, archived_deals=1, todos=1, notes=1, custom_fields=1)
    assert merge.merge_companies(s, a, actor_id=1, reason="same org") == plan
    assert all(service.get_contact(cid)['company_id'] == s and service.get_contact(cid)['company'] == 'Kept Org' for cid in contacts)
    assert all(service.get_deal(did)['company_id'] == s for did in deals)
    assert [service.get_contact(cid)['updated_at'] for cid in contacts] == before_contacts
    assert [service.get_deal(did)['updated_at'] for did in deals] == before_deals
    survivor = service.get_company_detail(s)
    assert len(survivor['contacts']) == 2
    assert any(row['id'] == activity['id'] for row in survivor['activity'])
    assert survivor['domain'] == 'dupe.example'
    assert survivor['notes'] == 'kept\n\nFrom Duplicate Org: old'
    absorbed = service.get_company(a)
    assert absorbed['status'] == 'archived' and absorbed['merged_into_id'] == s
    notes = chatter_service.get_chatter('company', s)
    assert any(n['message'] == f'[Merged from company #{a}] old' for n in notes)
    assert any(n['message'].startswith(f'Merged company #{a}') and n['author_id'] == 1 for n in notes)
    notes = chatter_service.get_chatter('company', a)
    assert [n['message'] for n in notes] == [f'Archived — Merged into company #{s}: same org', 'old']
    assert notes[0]['author_id'] == 1
    values = pg_fetchall("SELECT field_id, value FROM crm_field_values WHERE entity_type = 'company' AND entity_id = %s", (s,))
    assert {v['field_id']: v['value'] for v in values} == {fields[0]: 'west', fields[1]: 'kept'}
    assert service.resolve_or_create_company_ids(['Duplicate Org']) == {'Duplicate Org': s}


def test_chain_lifecycle_and_rename(pg_db):
    a, b, c = companies()
    contact = service.create_contact('Robin', company_id=b)['id']
    with pytest.raises(ValueError, match='already has'):
        merge.merge_companies(b, a, actor_id=None, reason='why', rename_to=' first org ')
    assert service.get_company(a)['status'] == 'active'
    assert service.get_company(b)['name'] == 'Second Org'
    service.update_company(c, status='archived', archive_reason='old')
    with pytest.raises(ValueError, match='archived'):
        merge_into(c, a)
    service.update_company(c, status='active')
    merge_into(b, a)
    with pytest.raises(ValueError, match='already merged'):
        merge_into(b, a)
    with pytest.raises(ValueError, match='cannot be restored'):
        service.update_company(a, status='active')
    with pytest.raises(psycopg2.errors.CheckViolation):
        pg_execute("UPDATE companies SET status = 'active' WHERE id = %s", (a,))
    # Alias rename stays legal and releases its old spelling.
    service.update_company(a, name='Former First Org')
    merge_into(c, b)
    assert service.get_company(a)['merged_into_id'] == b  # no chain compression
    assert service.resolve_or_create_company_ids(['Former First Org']) == {'Former First Org': c}
    assert service.create_contact('Taylor', company_id=a)['company_id'] == c
    assert service.update_contact(contact, company_id=a)['company_id'] == c
    deal = service.create_deal('Opportunity', company_id=a)
    assert deal['company_id'] == c
    assert service.update_deal(deal['id'], company_id=a)['company_id'] == c
    # Deleting a survivor releases its direct absorbed rows back to archived identities.
    service.delete_company(c)
    assert service.get_company(b)['merged_into_id'] is None
    assert service.resolve_or_create_company_ids(['Former First Org']) == {'Former First Org': b}


def test_rename_and_archived_source(pg_db):
    a, b, _ = companies()
    existing = service.create_contact('Robin', company='Second Org', company_id=b)['id']
    moved = service.create_contact('Taylor', company_id=a)['id']
    service.update_company(a, status='archived', archive_reason='duplicate')
    plan = merge.merge_companies(b, a, actor_id=None, reason='same org', rename_to=' Final Org ')
    assert plan['survivor']['name'] == 'Final Org'
    assert service.get_contact(existing)['company'] == 'Final Org'
    assert service.get_contact(moved)['company'] == 'Final Org'
    assert len(chatter_service.get_chatter('company', a)) == 2


def race(monkeypatch, dsn, first, second):
    """Hold the first service transaction before commit, observe the other's wait, release.

    Both SQL lock waits and thread joins are bounded. Checking pg_blocking_pids proves
    actual overlap rather than relying on a scheduler-dependent sleep.
    """
    original = postgres.get_connection
    held, release, entered = threading.Event(), threading.Event(), threading.Event()
    errors, results, pids = [], {}, {}
    paused = set()

    @contextmanager
    def connection():
        name = threading.current_thread().name
        with original() as conn:
            conn.cursor().execute("SET LOCAL statement_timeout = '5s'")
            pids[name] = conn.get_backend_pid()
            if name == 'merge-second':
                entered.set()
            yield conn
            if name == 'merge-first' and name not in paused:
                paused.add(name)
                held.set()
                if not release.wait(5):
                    raise AssertionError('first transaction was never released')

    for module in (postgres, service, merge):
        monkeypatch.setattr(module, 'get_connection', connection)

    def run(name, operation):
        try:
            results[name] = operation()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(name, op), name=name, daemon=True)
               for name, op in [('merge-first', first), ('merge-second', second)]]
    threads[0].start()
    try:
        assert held.wait(5), errors
        threads[1].start()
        assert entered.wait(5), errors
        deadline = time.monotonic() + 3
        waiting = False
        with psycopg2.connect(dsn) as observer:
            while time.monotonic() < deadline:
                with observer.cursor() as cur:
                    cur.execute('SELECT cardinality(pg_blocking_pids(%s))', (pids['merge-second'],))
                    waiting = cur.fetchone()[0] > 0
                if waiting:
                    break
                time.sleep(.01)
        assert waiting, 'second transaction did not wait on the first'
    finally:
        release.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(7)
    assert not any(thread.is_alive() for thread in threads)
    assert not errors, errors
    return results


@pytest.mark.parametrize('kind', ['contact', 'deal'])
@pytest.mark.parametrize('writer_first', [False, True])
@pytest.mark.parametrize('other_company', [False, True])
def test_save_vs_merge(pg_db, monkeypatch, kind, writer_first, other_company):
    a, b, c = companies()
    record = (service.create_contact('Robin', company_id=b) if kind == 'contact'
              else service.create_deal('Opportunity', company_id=b))
    update = service.update_contact if kind == 'contact' else service.update_deal
    get = service.get_contact if kind == 'contact' else service.get_deal
    def writer():
        return update(record['id'], company_id=a if other_company else b)

    def merger():
        return merge_into(c, b)

    race(monkeypatch, pg_db, writer if writer_first else merger, merger if writer_first else writer)
    assert get(record['id'])['company_id'] == (a if other_company else c)


@pytest.mark.parametrize('writer_first', [False, True])
@pytest.mark.parametrize('chained', [False, True])
@pytest.mark.parametrize('survivor_first', [False, True])
def test_assignment_vs_merge(pg_db, monkeypatch, writer_first, chained, survivor_first):
    a, b, c = companies()
    if survivor_first:
        b, c = c, b
    if chained:
        merge_into(b, a)
    def writer():
        return service.create_contact('Robin', company_id=a if chained else b)

    def merger():
        return merge_into(c, b)

    race(monkeypatch, pg_db, writer if writer_first else merger, merger if writer_first else writer)
    assert pg_fetchone("SELECT company_id FROM contacts WHERE name = 'Robin'")['company_id'] == c


@pytest.mark.parametrize('writer_first', [False, True])
def test_assignment_vs_delete(pg_db, monkeypatch, writer_first):
    a, b, _ = companies()
    merge_into(b, a)
    def writer():
        return service.create_contact('Robin', company_id=a)

    def deleter():
        return service.delete_company(b)

    race(monkeypatch, pg_db, writer if writer_first else deleter, deleter if writer_first else writer)
    assert pg_fetchone("SELECT company_id FROM contacts WHERE name = 'Robin'")['company_id'] == (None if writer_first else a)
