"""Deterministic pagination guard (issue #58).

A reader that sorts by a non-unique column and then caps the result with ``LIMIT``
has no defined output. Postgres may break ties differently on each execution, so
between two reads a row can appear on two pages or on none — the classic
"rows repeat or vanish while scrolling" bug. The fix is a total order: every such
``ORDER BY`` must end on a term that is unique within the result set.

Ties are the normal case here, not a rare one. ``created_at``/``updated_at`` default
to ``now()``, which is TRANSACTION-start time, so every row written inside one
transaction carries a byte-identical timestamp — a CSV import batch, ``seed_data``,
a merge that copies notes, any multi-row CRM write. Other sort keys are worse:
``tasks.completed`` is a 0/1 flag, ``tasks.due_date`` is TEXT defaulting to ``''``.

Three layers, each covering the previous one's blind spot:

1. ``test_capped_readers_have_a_total_order`` statically scans every non-test module
   under ``backend/`` for the failure shape. It catches a reader added tomorrow, which
   no behavioral test can.
2. The scan cannot resolve an ``ORDER BY`` assembled from a variable, so
   ``test_dynamic_order_by_*`` drives those readers and asserts on the SQL they
   actually emit.
3. ``test_tied_timestamps_paginate_without_dupes_or_gaps`` (integration) proves
   against real Postgres that the shape delivers the behavior — rows sharing one
   ``now()`` page cleanly.

The scanner's own correctness is pinned by ``test_scanner_rejects_...``/
``_accepts_...`` and by ``test_scan_actually_examined_the_backend``: a scanner that
silently matches nothing would be a permanent green, which is worse than no scanner.
"""

import ast
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent

# Columns that are unique on their own, so an ORDER BY ending on one is already total.
# Each entry is a real constraint, not a convention:
#   id        — PRIMARY KEY on every table a reader here touches (SERIAL, BIGSERIAL or
#               a uuid4 TEXT; arbitrary ordering is fine, uniqueness is the point).
#   filename  — assistant_context_files.filename TEXT NOT NULL UNIQUE.
#   seq       — unique per conversation; both readers using it are scoped to one
#               conversation_id, so (conversation_id, seq) is the effective key.
UNIQUE_TIEBREAKERS = frozenset({"id", "filename", "seq"})

# Clauses that may follow ORDER BY and are not part of the ordering.
_TRAILING = re.compile(
    r"\b(LIMIT|OFFSET|FOR\s+UPDATE|FOR\s+SHARE|FETCH|ON\s+CONFLICT|RETURNING)\b", re.I)
_DIRECTION = re.compile(r"\b(ASC|DESC|NULLS\s+FIRST|NULLS\s+LAST)\b", re.I)
_PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def _sql_literals(tree: ast.AST):
    """Yield ``(lineno, sql)`` for every string literal that could be SQL.

    Uses the AST rather than a line regex for two reasons: Python concatenates adjacent
    string literals at parse time, so a statement split across source lines arrives as
    ONE node; and an f-string arrives as a JoinedStr whose literal parts can be stitched
    back together with each interpolation rendered as ``{expr}``.

    Bare string *expressions* are skipped — that is every docstring, several of which
    quote ``ORDER BY`` while describing this very invariant.
    """
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, (ast.Constant, ast.JoinedStr)):
            skip.add(id(node.value))
        if isinstance(node, ast.JoinedStr):
            skip.update(id(part) for part in node.values)

    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value
        elif isinstance(node, ast.JoinedStr):
            parts = []
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    parts.append(part.value)
                else:
                    # Render as {expr} so two different interpolations stay
                    # distinguishable when GROUP BY coverage is checked below.
                    parts.append("{" + ast.unparse(part.value) + "}")
            yield node.lineno, "".join(parts)


def _split_terms(clause: str) -> list[str]:
    """Split a comma-separated SQL clause at top level (commas inside parens belong to
    a function call like ``COALESCE(a, b)``, not to the clause)."""
    terms: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in clause:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            terms.append("".join(current))
            current = []
        else:
            current.append(ch)
    terms.append("".join(current))
    return [t.strip() for t in terms if t.strip()]


def _normalize(term: str) -> str:
    return " ".join(_DIRECTION.sub("", term).split()).lower()


def order_by_verdict(sql: str) -> str | None:
    """``None`` if this statement's capped ORDER BY is totally ordered (or the question
    is not decidable from source), else a human-readable reason it is not.

    Deliberately silent on what it cannot resolve — a term that is entirely an
    interpolation, or an ORDER BY whose LIMIT lives in a different string. Those are
    covered by the behavioral tests below. The result is a check with false negatives
    and no false positives, which needs no allowlist and therefore cannot rot.
    """
    flat = " ".join(sql.split())
    limits = [m.start() for m in re.finditer(r"\bLIMIT\b", flat, re.I)]
    if not limits:
        return None  # uncapped: the whole set comes back, so nothing can dupe or vanish
    last_limit = limits[-1]
    order_bys = [m for m in re.finditer(r"\bORDER\s+BY\b", flat, re.I) if m.start() < last_limit]
    if not order_bys:
        return None
    # The LAST ORDER BY before the LIMIT is the statement's own; earlier ones belong to
    # an aggregate (ARRAY_AGG(id ORDER BY id)) or a window frame.
    clause = flat[order_bys[-1].end():last_limit]
    trailing = _TRAILING.search(clause)
    if trailing:
        clause = clause[:trailing.start()]
    terms = _split_terms(clause)
    if not terms:
        return None

    # A grouped query is already total once the ORDER BY covers the whole GROUP BY key:
    # each group is exactly one output row and the key identifies it.
    group_by = re.search(
        r"\bGROUP\s+BY\b(.*?)(?=\bHAVING\b|\bORDER\s+BY\b|\bLIMIT\b|$)", flat, re.I)
    if group_by:
        ordered = {_normalize(t) for t in terms}
        if all(_normalize(k) in ordered for k in _split_terms(group_by.group(1))):
            return None

    last = terms[-1]
    bare = _normalize(_PLACEHOLDER.sub("", last))
    if not bare:
        return None  # the whole term is interpolated — undecidable from source
    match = re.fullmatch(r"(?:\w+\.)?(\w+)", bare)
    if match and match.group(1) in UNIQUE_TIEBREAKERS:
        return None
    return (f"ORDER BY ends on {last!r}, which is not unique — a tie under the LIMIT "
            f"makes the page non-reproducible. Append an id term (issue #58).")


def _scan_backend() -> tuple[list[str], int]:
    """Return ``(failures, statements_examined)`` over every non-test backend module."""
    failures: list[str] = []
    examined = 0
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if rel.parts[0] in ("tests", ".venv", "venv"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, sql in _sql_literals(tree):
            flat = " ".join(sql.split())
            if not (re.search(r"\bORDER\s+BY\b", flat, re.I)
                    and re.search(r"\bLIMIT\b", flat, re.I)):
                continue
            examined += 1
            verdict = order_by_verdict(sql)
            if verdict:
                failures.append(f"{rel}:{lineno}: {verdict}")
    return failures, examined


# ── the scanner's own guard rails ────────────────────────────────────────────────
#
# A static check that quietly matches nothing looks exactly like a passing one. These
# pin both directions before the sweep below is allowed to mean anything.

@pytest.mark.parametrize("sql", [
    "SELECT * FROM alerts ORDER BY created_at DESC LIMIT %s",
    "SELECT * FROM t WHERE x = %s ORDER BY updated_at DESC LIMIT %s OFFSET %s",
    "SELECT * FROM tasks ORDER BY completed ASC, due_date ASC LIMIT 20",
    "SELECT a.* FROM activity_log a ORDER BY a.created_at DESC LIMIT %s",
    "SELECT * FROM d ORDER BY value DESC NULLS LAST LIMIT 5",
    # Grouped, but the ORDER BY covers only half the group key.
    "SELECT lower(title), contact_id, COUNT(*) FROM deals GROUP BY lower(title), contact_id "
    "ORDER BY COUNT(*) DESC, lower(title) ASC LIMIT %s",
])
def test_scanner_rejects_a_non_total_capped_order(sql):
    assert order_by_verdict(sql) is not None, f"scanner failed to flag: {sql}"


@pytest.mark.parametrize("sql", [
    # Totally ordered by an id term.
    "SELECT * FROM alerts ORDER BY created_at DESC, id DESC LIMIT %s",
    "SELECT a.* FROM activity_log a ORDER BY a.created_at DESC, a.id DESC LIMIT %s",
    "SELECT * FROM t ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s",
    # Ordered by a column that is unique on its own.
    "SELECT * FROM assistant_context_files ORDER BY updated_at DESC, filename LIMIT %s",
    "SELECT content FROM assistant_messages WHERE conversation_id = %s ORDER BY seq DESC LIMIT 1",
    # Uncapped — no page to be inconsistent about.
    "SELECT * FROM contacts WHERE company_id = %s ORDER BY name ASC",
    # Grouped, ORDER BY covers the whole group key.
    "SELECT lower(title), contact_id, COUNT(*) FROM deals GROUP BY lower(title), contact_id "
    "ORDER BY COUNT(*) DESC, lower(title) ASC, contact_id ASC LIMIT %s",
    # The earlier ORDER BY belongs to an aggregate, not to the statement.
    "SELECT k, ARRAY_AGG(id ORDER BY id) FROM t GROUP BY k ORDER BY k ASC LIMIT %s",
    # Undecidable: the final term is entirely interpolated.
    "SELECT * FROM contacts ORDER BY {order_by} LIMIT %s OFFSET %s",
    # Decidable through an interpolated direction.
    "SELECT * FROM companies ORDER BY {sort_col} {direction}, id {direction} LIMIT %s",
    # A trailing lock clause is not part of the ordering.
    "SELECT id FROM users ORDER BY is_active DESC, id ASC LIMIT 1 FOR UPDATE",
])
def test_scanner_accepts_a_total_capped_order(sql):
    assert order_by_verdict(sql) is None, f"scanner wrongly flagged: {sql}"


def test_scanner_reads_split_and_interpolated_literals():
    """The AST walk must see a statement Python assembled from several source pieces —
    otherwise the sweep would silently skip most real readers."""
    module = ast.parse(
        'x = ("SELECT * FROM t ORDER BY "\n'
        '     "created_at DESC LIMIT %s")\n'
        'y = f"SELECT * FROM t {where} ORDER BY updated_at DESC LIMIT %s"\n'
        '"""A docstring mentioning ORDER BY created_at DESC LIMIT 5."""\n'
    )
    found = [sql for _, sql in _sql_literals(module)]
    assert any("ORDER BY created_at DESC LIMIT %s" in s for s in found), \
        "adjacent string literals were not stitched into one statement"
    assert any("{where}" in s and "ORDER BY updated_at DESC LIMIT %s" in s for s in found), \
        "f-string parts were not stitched into one statement"
    assert not any("A docstring mentioning" in s for s in found), \
        "docstrings must be skipped or prose would be scanned as SQL"


def test_scan_actually_examined_the_backend():
    """Fail if the sweep stopped finding capped readers. Guards against a path or parse
    change turning the check below into a vacuous pass."""
    _, examined = _scan_backend()
    assert examined >= 25, (
        f"only {examined} capped ORDER BY statements were found in backend/ — the "
        "scanner is probably broken, not the codebase suddenly clean")


# ── the sweep ────────────────────────────────────────────────────────────────────

def test_capped_readers_have_a_total_order():
    failures, _ = _scan_backend()
    assert not failures, (
        "capped readers with a non-reproducible page order (issue #58):\n  "
        + "\n  ".join(failures))


# ── readers whose ORDER BY the scan cannot resolve ───────────────────────────────

class _Recorder:
    """Records the SQL passed to the pg helpers; returns nothing useful."""

    def __init__(self):
        self.sql: list[str] = []

    def fetchall(self, sql, params=()):
        self.sql.append(" ".join(sql.split()))
        return []

    def fetchone(self, sql, params=()):
        self.sql.append(" ".join(sql.split()))
        return {"cnt": 0}

    def capped(self) -> list[str]:
        return [s for s in self.sql if re.search(r"\bORDER\s+BY\b", s, re.I)
                and re.search(r"\bLIMIT\b", s, re.I)]


def _assert_recorded_orders_are_total(recorder: _Recorder):
    capped = recorder.capped()
    assert capped, "the reader emitted no capped ORDER BY to check"
    for sql in capped:
        verdict = order_by_verdict(sql)
        assert verdict is None, f"{verdict}\n  emitted SQL: {sql}"


def test_order_by_fragment_constants_are_total():
    """Readers that interpolate an allow-listed ORDER BY fragment show the scanner only
    a placeholder, so the fragments themselves are checked directly. Reading the live
    constants means a sort option added later is covered without editing this test —
    which is how ``_TASK_SORTS`` (arriving with #77, where ``list_tasks``' literal
    ORDER BY becomes a fragment lookup) is already accounted for here.
    """
    from crm import service

    checked = 0
    for name in ("_CONTACT_SORTS", "_TASK_SORTS"):
        fragments = getattr(service, name, None)
        if fragments is None:
            continue
        for key, fragment in fragments.items():
            verdict = order_by_verdict(f"SELECT 1 FROM t ORDER BY {fragment} LIMIT %s")
            assert verdict is None, f"{name}[{key!r}] -> {fragment!r}: {verdict}"
            checked += 1
    assert checked, "no ORDER BY fragment constants found — was one renamed or removed?"


@pytest.fixture
def crm_recorder(monkeypatch):
    from crm import service

    r = _Recorder()
    monkeypatch.setattr(service, "pg_fetchall", r.fetchall)
    monkeypatch.setattr(service, "pg_fetchone", r.fetchone)
    return r


def _contact_sort_keys():
    from crm.service import _CONTACT_SORTS

    # Parametrizing off the live dict means a sort option added later is covered here
    # automatically instead of needing this list edited.
    return sorted(_CONTACT_SORTS) + ["not-a-real-sort"]  # the last exercises the fallback


@pytest.mark.parametrize("sort", _contact_sort_keys())
def test_dynamic_order_by_contact_lists(crm_recorder, sort):
    from crm import service

    service.list_contacts(sort=sort)
    service.search_contacts("acme", sort=sort)
    _assert_recorded_orders_are_total(crm_recorder)


@pytest.mark.parametrize("sort", ["name", "industry", "created_at", "updated_at", "bogus"])
def test_dynamic_order_by_company_list(crm_recorder, sort):
    from crm import service

    service.list_companies(sort=sort)
    _assert_recorded_orders_are_total(crm_recorder)


def test_dynamic_order_by_deal_search(crm_recorder):
    from crm import service

    for sort in sorted(service._DEAL_SORTS) + ["bogus"]:
        for direction in ("asc", "desc"):
            service.search_deals(search="q", sort_by=sort, sort_dir=direction)
    _assert_recorded_orders_are_total(crm_recorder)


@pytest.mark.parametrize("mode", ["status", "search", "default"])
def test_dynamic_order_by_todo_list(monkeypatch, mode):
    from crm import gtd_service

    r = _Recorder()
    monkeypatch.setattr(gtd_service, "pg_fetchall", r.fetchall)
    kwargs = {"status": "next_action"} if mode == "status" else \
        {"search": "milk"} if mode == "search" else {}
    gtd_service.list_todos(**kwargs)
    _assert_recorded_orders_are_total(r)


def test_scoring_backfill_orders_are_total(monkeypatch):
    """``backfill_scores``/``_stale_ids`` build ORDER BY and LIMIT in separate literals,
    so the source scan skips them by design — checked here on the emitted SQL instead."""
    from crm import scoring_service

    r = _Recorder()
    monkeypatch.setattr(scoring_service, "pg_fetchall", r.fetchall)
    scoring_service.backfill_scores(scope="all")
    scoring_service.backfill_scores(scope="null")
    _assert_recorded_orders_are_total(r)


# ── the behavior the shape is supposed to buy ────────────────────────────────────

@pytest.mark.integration
def test_tied_timestamps_paginate_without_dupes_or_gaps():
    """Rows written in ONE transaction share a byte-identical ``now()``. Page through
    them and assert every row is seen exactly once — the property the id tiebreaker
    exists to provide, proven against real Postgres rather than inferred from SQL text.
    """
    import os

    import psycopg2

    from core import postgres

    admin_dsn = os.getenv("TEST_ADMIN_DSN", "postgresql://cake:cake_dev@localhost:5432/cake")
    dbname = f"cakecrm_it_pagination_{os.getpid()}"
    admin = psycopg2.connect(admin_dsn)
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        cur.execute(f'CREATE DATABASE "{dbname}"')
    admin.close()

    prev = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = admin_dsn.rsplit("/", 1)[0] + f"/{dbname}"
    try:
        postgres.close_pool()
        postgres.init_pool()
        postgres.run_migrations()

        from crm import service

        total, page_size = 25, 5
        with postgres.get_connection() as conn:
            with conn.cursor() as cur:
                # One transaction => one now() => every row's created_at is identical.
                cur.executemany(
                    "INSERT INTO companies (name) VALUES (%s)",
                    [(f"Tied Co {i:02d}",) for i in range(total)],
                )
            conn.commit()

        distinct = postgres.pg_fetchone(
            "SELECT COUNT(DISTINCT created_at) AS n FROM companies")
        assert distinct["n"] == 1, (
            "the fixture failed to create the tie this test is about "
            f"({distinct['n']} distinct created_at values)")

        def sweep() -> list[int]:
            seen: list[int] = []
            for offset in range(0, total, page_size):
                page = service.list_companies(
                    offset=offset, limit=page_size, sort="created_at")
                seen.extend(row["id"] for row in page["companies"])
            return seen

        first = sweep()
        assert len(first) == total, f"expected {total} rows across the pages, got {len(first)}"
        assert len(set(first)) == total, (
            "a row was returned on two pages while another was skipped: "
            f"{len(first) - len(set(first))} duplicate(s)")

        # Same query, run again: a total order is reproducible, an arbitrary one is not.
        assert sweep() == first, "paging the same tied rows twice produced a different order"

        # The detail rollups are capped, not paged — assert their order is at least stable.
        cid = first[0]
        assert (service.get_company_detail(cid)["contacts"]
                == service.get_company_detail(cid)["contacts"])
    finally:
        postgres.close_pool()
        if prev is not None:
            os.environ["DATABASE_URL"] = prev
        else:
            os.environ.pop("DATABASE_URL", None)
        admin = psycopg2.connect(admin_dsn)
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (dbname,))
            cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        admin.close()
