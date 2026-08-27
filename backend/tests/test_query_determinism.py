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

Four layers, each covering the previous one's blind spot:

1. ``test_capped_readers_have_a_total_order`` statically scans every non-test module
   under ``backend/`` for the failure shape. It catches a reader added tomorrow, which
   no behavioral test can.
2. The scan cannot resolve an ``ORDER BY`` assembled at runtime, so it reports those as
   ``unknown`` rather than passing them. ``test_undecidable_sites_are_registered`` pins
   that set exactly, and ``test_dynamic_order_by_*`` / ``test_order_by_fragment_*``
   drive those readers and assert on the SQL they really emit. Without the third state a
   reader could opt out of the whole guard by moving its ORDER BY into a variable.
3. ``test_hardened_uncapped_readers_keep_their_tiebreaker`` covers the readers that were
   given a tiebreaker *before* they have a cap — invisible to the scan by definition,
   and the reason issue #59 can paginate the pipeline board safely.
4. ``test_tied_timestamps_paginate_without_dupes_or_gaps`` (integration) proves against
   real Postgres that the shape delivers the behavior — rows sharing one ``now()`` page
   cleanly. This is the layer that actually reproduces the bug: with the tiebreaker
   removed it reports a duplicated row and a skipped one.

The scanner's own correctness is pinned in all three directions
(``test_scanner_rejects_...`` / ``_accepts_...`` / ``_reports_runtime_assembled_...``)
and its reach by ``test_scan_actually_examined_the_backend``, per module rather than as
one total — ``crm`` alone would satisfy a repo-wide count. A sweep that silently stops
matching is a permanent green, which is worse than no sweep because it reads as coverage.
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


def _split_terms(clause: str, masked: str | None = None) -> list[str]:
    """Split a comma-separated SQL clause at top level.

    A comma inside parens belongs to a call like ``COALESCE(a, b)``, not to the clause.
    Delimiters are located in ``masked`` — the same string with quoted contents blanked —
    so a bracket or comma inside a literal (``btrim(n, E') ')``) cannot unbalance the
    depth count; the returned text is always sliced from ``clause`` itself.
    """
    scan = masked if masked is not None and len(masked) == len(clause) else clause
    bounds: list[int] = []
    depth = 0
    for i, ch in enumerate(scan):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "," and depth == 0:
            bounds.append(i)
    terms, start = [], 0
    for i in bounds:
        terms.append(clause[start:i])
        start = i + 1
    terms.append(clause[start:])
    return [t.strip() for t in terms if t.strip()]


def _normalize(term: str) -> str:
    return " ".join(_DIRECTION.sub("", term).split()).lower()


def _mask_placeholders(text: str) -> str:
    """Blank out ``{...}`` interpolations, preserving length so offsets still line up.

    A placeholder's NAME is not SQL, and matching keywords inside one is a false
    positive with real bite: ``LIMIT {limit}`` otherwise reads as two caps, the second
    of which has no ordering in its span and fails a perfectly good query.
    """
    return _PLACEHOLDER.sub(lambda m: "\x00" * len(m.group()), text)


def _mask_quoted(text: str) -> str:
    """Blank out the CONTENTS of quoted literals, preserving length.

    Keywords and punctuation inside a value are data: ``WHERE operation = 'LIMIT'``
    must not read as a cap, and the ``)`` in ``btrim(n, E' \\t')`` must not unbalance
    term splitting. The quote characters themselves are kept so offsets and the
    surrounding syntax are unchanged.
    """
    out: list[str] = []
    quote: str | None = None
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if quote:
            if ch == quote:
                out.append(ch)
                quote = None
            else:
                out.append("\x00")
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _prepare(sql: str) -> tuple[str, str]:
    """Return ``(flat, masked)`` for one SQL string.

    ``flat`` is the statement with comments removed and whitespace collapsed — the text
    verdict messages quote and terms are sliced from. ``masked`` is the same string with
    placeholder and quoted-literal contents blanked out, so keyword searches see only
    real syntax. The two are the same length, so an offset found in one indexes the other.

    Order matters: comments must be stripped BEFORE whitespace is collapsed. A ``--``
    comment ends at a newline, so flattening first lets it swallow the remainder of the
    statement — which silently erased the ORDER BY and LIMIT of every reader whose SQL
    carries an explanatory comment above its ordering, this diff's own included.
    """
    flat = " ".join(_strip_sql_comments(sql).split())
    return flat, _mask_placeholders(_mask_quoted(flat))


def _strip_sql_comments(sql: str) -> str:
    """Remove ``--`` line comments and ``/* */`` blocks, respecting quoted text.

    Load-bearing, not tidiness, in both directions. This file's own SQL edits put ``--``
    notes directly above the ORDER BY they explain, and flattening whitespace first would
    splice such a note into the statement — so a comment that merely mentioned an
    ordering could be read AS the ordering. But stripping naively is just as bad: a
    literal ``WHERE marker = '--'`` would swallow the rest of the line including its
    ``LIMIT``, and a capped, unordered query would read as uncapped. Hence the quote
    tracking rather than a plain regex.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    quote: str | None = None
    while i < n:
        ch = sql[i]
        if quote:
            out.append(ch)
            if ch == quote:
                # '' inside a single-quoted string is an escaped quote, not a close.
                if quote == "'" and i + 1 < n and sql[i + 1] == "'":
                    out.append(sql[i + 1])
                    i += 2
                    continue
                quote = None
            i += 1
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
        elif sql.startswith("--", i):
            while i < n and sql[i] != "\n":
                i += 1
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
        else:
            out.append(ch)
            i += 1
    return "".join(out)


_CAP = re.compile(r"\b(?:LIMIT|FETCH\s+(?:FIRST|NEXT))\b", re.I)
_OFFSET = re.compile(r"\bOFFSET\b", re.I)


# How far after a LIMIT/FETCH an OFFSET may sit and still belong to that same cap.
# `LIMIT %s OFFSET %s` puts about nine characters between them; thirty is slack.
_OFFSET_ADJACENT = 30


def _caps(masked: str) -> list[re.Match]:
    """Where this statement bounds its result set, in textual order.

    ``LIMIT`` and ``FETCH FIRST/NEXT`` are caps. A bare ``OFFSET`` is one too — it is a
    valid paginator on its own, and ignoring it would let an unordered
    ``SELECT … OFFSET %s`` bypass the guard entirely.

    An OFFSET is skipped only when it TRAILS a LIMIT/FETCH closely enough to be part of
    that same cap (the ordinary ``LIMIT %s OFFSET %s``), where counting it again would
    invent a second, orderless cap. Suppressing every OFFSET whenever the statement
    happened to contain a LIMIT anywhere was the earlier rule, and it hid a subquery's
    own bare-OFFSET cap behind an unrelated outer LIMIT.
    """
    caps = list(_CAP.finditer(masked))
    offsets = [m for m in _OFFSET.finditer(masked)
               if not any(0 <= m.start() - c.end() <= _OFFSET_ADJACENT for c in caps)]
    return sorted(caps + offsets, key=lambda m: m.start())


def _is_capped(masked: str) -> bool:
    return bool(_CAP.search(masked) or _OFFSET.search(masked))


def _statement_orderings(flat: str, masked: str):
    """Yield ``(clause, masked_clause, cap_text, cap_offset)`` for each cap, where the
    first two are ``None`` if that cap has no ordering of its own.

    Each cap is paired with an ORDER BY lying between it and the PREVIOUS cap — not
    merely the nearest one before it. The distinction is what stops a cap from
    borrowing an inner query's ordering: in
    ``SELECT * FROM (SELECT * FROM a ORDER BY id LIMIT 5) s LIMIT 10`` the outer cap has
    no ordering of its own, and nearest-preceding would hand it the subquery's ``id`` and
    call the statement total. Confining the search to the span since the last cap gives
    the outer LIMIT nothing, which is the truth.

    It still reads correctly for a correlated ``LATERAL (… ORDER BY seq DESC LIMIT 1)``
    beside an outer ``ORDER BY … LIMIT %s`` (two independent verdicts), and an
    aggregate's ``ARRAY_AGG(x ORDER BY x)`` never stands in for the statement's own
    ordering, since the last ORDER BY in the span wins.
    """
    order_bys = [m for m in re.finditer(r"\bORDER\s+BY\b", masked, re.I)]
    prev_cap_end = 0
    for cap in _caps(masked):
        in_span = [m for m in order_bys
                   if m.start() >= prev_cap_end and m.end() <= cap.start()]
        cap_text = flat[cap.start():cap.start() + 40]
        prev_cap_end = cap.end()
        if not in_span:
            yield None, None, cap_text, cap.start()
            continue
        clause = flat[in_span[-1].end():cap.start()]
        mclause = masked[in_span[-1].end():cap.start()]
        trailing = _TRAILING.search(mclause)
        if trailing:
            clause = clause[:trailing.start()]
            mclause = mclause[:trailing.start()]
        yield clause, mclause, cap_text, cap.start()


def judge(sql: str) -> tuple[str, str]:
    """Classify one SQL string as ``("ok"|"fail"|"unknown", detail)``.

    Three states, not two, and the third is the point. ``unknown`` means the ordering is
    assembled at runtime (an interpolated term, or a ``LIMIT`` whose ORDER BY lives in a
    different string), so source alone cannot settle it. Those sites are NOT waved
    through: ``test_undecidable_sites_are_registered`` pins the exact set, so a new one
    fails until someone adds a behavioral test for it. Collapsing ``unknown`` into
    ``ok`` — the earlier design — let a reader opt out of the guard just by moving its
    ORDER BY into a variable.

    Known limits, stated so nobody mistakes this for a SQL parser:

    * Uniqueness is inferred from the final term's NAME, so an alias like
      ``status AS id``, or a join that multiplies rows and leaves a parent ``id``
      non-unique, would pass.
    * ``seq`` is accepted on the strength of both current readers being scoped to one
      conversation; it is unique only within one.
    * Caps are paired with orderings by TEXT SPAN, not by SELECT scope, so an ORDER BY
      that itself contains a capped scalar subquery can be reported as a false ``fail``.
    * A statement assembled across several separate literals carries neither keyword in
      any one of them and is not seen here at all — those readers are covered by the
      behavioral tests instead (see ``UNDECIDABLE_SITES``).

    The first two would pass a broken reader; the third is loud and the fourth is
    covered elsewhere. Closing the first two properly means cardinality analysis over a
    real parse tree, which is more machinery than this invariant is worth.
    """
    flat, masked = _prepare(sql)
    if not _is_capped(masked):
        return "ok", "uncapped"  # the whole set comes back: nothing can dupe or vanish
    is_query = bool(re.search(r"\bSELECT\b", masked, re.I))

    # Every cap is judged, and a definite failure outranks an undecidable one — an early
    # `unknown` must not stop a later, provably broken cap from being reported.
    unknown: str | None = None
    for clause, mclause, limit_text, limit_at in _statement_orderings(flat, masked):
        # A placeholder can only be hiding an ordering if it sits BEFORE the cap;
        # `LIMIT {n}` plainly cannot supply one.
        hidden_order = bool(_PLACEHOLDER.search(flat[:limit_at]))

        if clause is None:
            # A cap with no ordering at all is the most non-reproducible shape there is.
            # Only judged for something that actually looks like a query — plenty of
            # prose ("exceeds the 10 MB limit") contains the word.
            if not is_query:
                continue
            if hidden_order:
                unknown = unknown or f"capped, ordering may be interpolated: {limit_text!r}"
                continue
            return "fail", (f"{limit_text!r} caps a SELECT that has no ORDER BY at all — "
                            "the rows it returns are whatever the plan happens to emit.")

        terms = _split_terms(clause, mclause)
        if not terms:
            unknown = unknown or f"empty ORDER BY before {limit_text!r}"
            continue

        # A grouped query is already total once the ORDER BY covers the whole GROUP BY
        # key: each group is exactly one output row and the key identifies it. Applied
        # only to a single-SELECT statement — in a CTE or subquery the GROUP BY found
        # textually may belong to an inner scope and prove nothing about the outer rows.
        if len(re.findall(r"\bSELECT\b", masked, re.I)) == 1:
            # Located on `masked` so keyword text sitting inside a quoted VALUE cannot
            # pose as a GROUP BY clause and hand this query a group key it does not have
            # — the one place the scanner could still fail silently. Sliced from `flat`,
            # because the comparison against the ORDER BY needs the real column text.
            group_by = re.search(
                r"\bGROUP\s+BY\b(.*?)(?=\bHAVING\b|\bORDER\s+BY\b|\bLIMIT\b|$)", masked, re.I)
            if group_by:
                lo, hi = group_by.span(1)
                keys = _split_terms(flat[lo:hi], masked[lo:hi])
                ordered = {_normalize(t) for t in terms}
                if keys and all(_normalize(k) in ordered for k in keys):
                    continue

        last = terms[-1]
        bare = _normalize(_PLACEHOLDER.sub("", last))
        if not bare:
            unknown = unknown or f"final ORDER BY term is interpolated: {last!r}"
            continue
        match = re.fullmatch(r"(?:\w+\.)?(\w+)", bare)
        if match and match.group(1) in UNIQUE_TIEBREAKERS:
            continue
        return "fail", (f"ORDER BY ends on {last!r}, which is not unique — a tie under "
                        f"{limit_text!r} makes the page non-reproducible. Append an id "
                        "term (issue #58).")
    if unknown:
        return "unknown", unknown
    return "ok", "every cap is totally ordered"


def order_by_verdict(sql: str) -> str | None:
    """``None`` when ``sql`` is not a proven-broken capped reader, else why it is.

    Thin wrapper over :func:`judge` for the behavioral tests, which see fully-resolved
    runtime SQL where ``unknown`` cannot arise.
    """
    state, detail = judge(sql)
    return detail if state == "fail" else None


def _function_at_line(tree: ast.AST) -> dict[int, str]:
    """``{line: enclosing function name}`` for every line inside a def in this module."""
    owner: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                # Walk order is outer-to-inner per branch, so a nested def wins its own
                # lines — which is what we want for a helper defined inside a reader.
                owner[line] = node.name
    return owner


def _scan_backend() -> tuple[list[str], dict[str, int], dict[str, int]]:
    """Return ``(failures, {package: examined}, {file::function: undecidable_count})``."""
    failures: list[str] = []
    examined: dict[str, int] = {}
    undecidable: dict[str, int] = {}
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(BACKEND)
        if rel.parts[0] in ("tests", ".venv", "venv"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            # Fail, never skip: a file this sweep could not read is a file whose readers
            # are unchecked, and silently passing over it is the exact failure mode the
            # sweep exists to prevent.
            failures.append(f"{rel}: could not be scanned ({exc.__class__.__name__}: {exc})")
            continue
        owner = _function_at_line(tree)
        for lineno, sql in _sql_literals(tree):
            _, masked = _prepare(sql)
            if not _is_capped(masked):
                continue
            # A capped literal is in scope if it orders rows or looks like a query;
            # `LIMIT` alone matches English prose ("exceeds the 10 MB limit").
            if not (re.search(r"\bORDER\s+BY\b", masked, re.I)
                    or re.search(r"\bSELECT\b", masked, re.I)):
                continue
            state, detail = judge(sql)
            if state == "fail":
                failures.append(f"{rel}:{lineno}: {detail}")
            elif state == "unknown":
                # Keyed by the ENCLOSING FUNCTION, not the file: two undecidable readers
                # in one file must stay distinguishable, or one becoming decidable while
                # another appeared would net out to no change and let the new one inherit
                # the old registration. A function name is also stable under the line
                # churn an exact line-number key would suffer.
                site = f"{rel}::{owner.get(lineno, '<module>')}"
                undecidable[site] = undecidable.get(site, 0) + 1
            examined[rel.parts[0]] = examined.get(rel.parts[0], 0) + 1
    return failures, examined, undecidable


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
    # No ordering at all — the most non-reproducible shape of the lot.
    "SELECT * FROM contacts LIMIT 20",
    "SELECT * FROM contacts WHERE status = %s LIMIT %s OFFSET %s",
    # A commented-out ordering must not be read as the ordering.
    "SELECT * FROM t\nORDER BY created_at DESC -- ORDER BY id\nLIMIT 10",
    "SELECT * FROM t /* ORDER BY id */ ORDER BY created_at DESC LIMIT 10",
    # An inner cap is judged on its own ordering, not the outer query's.
    "SELECT * FROM (SELECT * FROM a ORDER BY created_at LIMIT 5) s "
    "ORDER BY s.created_at DESC, s.id DESC LIMIT 10",
    # A GROUP BY inside a CTE proves nothing about the outer rows.
    "WITH g AS (SELECT tenant_id, COUNT(*) FROM events GROUP BY tenant_id) "
    "SELECT g.tenant_id, c.name FROM g JOIN contacts c ON TRUE "
    "ORDER BY g.tenant_id LIMIT 10",
    # An outer cap must not borrow the SUBQUERY's ordering: the outer rows are unordered.
    "SELECT * FROM (SELECT * FROM a ORDER BY id LIMIT 5) s LIMIT 10",
    # '--' inside a string literal is data, not the start of a comment; stripping it as
    # one would swallow the LIMIT and make this capped, unordered query read as uncapped.
    "SELECT * FROM t WHERE marker = '--' LIMIT 10",
    # An undecidable inner cap must not mask a provably broken outer one.
    "SELECT * FROM (SELECT * FROM a ORDER BY {s} LIMIT 5) x ORDER BY created_at LIMIT 10",
    # GROUP BY text inside a quoted VALUE must not hand the query a group key.
    "SELECT * FROM t WHERE note = 'GROUP BY status ORDER BY x' ORDER BY status LIMIT 5",
    # A bare OFFSET is a cap of its own; an unrelated outer LIMIT must not hide it.
    "SELECT * FROM (SELECT * FROM a ORDER BY created_at OFFSET 10) s ORDER BY id LIMIT 5",
])
def test_scanner_rejects_a_non_total_capped_order(sql):
    state, detail = judge(sql)
    assert state == "fail", f"scanner returned {state!r} ({detail}) for: {sql}"


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
    # Decidable through an interpolated direction.
    "SELECT * FROM companies ORDER BY {sort_col} {direction}, id {direction} LIMIT %s",
    # A trailing lock clause is not part of the ordering.
    "SELECT id FROM users ORDER BY is_active DESC, id ASC LIMIT 1 FOR UPDATE",
    # A subquery cap and the outer cap are each totally ordered on their own terms.
    "SELECT c.* FROM conversations c LEFT JOIN LATERAL ("
    "  SELECT content FROM messages WHERE conversation_id = c.id ORDER BY seq DESC LIMIT 1"
    ") u ON TRUE ORDER BY c.updated_at DESC, c.id DESC LIMIT %s OFFSET %s",
    # Prose that merely contains the word "limit" is not a query.
    "'{f.filename}' exceeds the 10 MB limit.",
    # A keyword inside a placeholder NAME is not a keyword — `LIMIT {limit}` is one cap.
    "SELECT * FROM t ORDER BY created_at DESC, id DESC LIMIT {limit}",
    "SELECT * FROM t {order_clause_unused} ORDER BY id LIMIT {limit} OFFSET {offset}",
])
def test_scanner_accepts_a_total_capped_order(sql):
    state, detail = judge(sql)
    assert state == "ok", f"scanner returned {state!r} ({detail}) for: {sql}"


@pytest.mark.parametrize("sql", [
    # The whole ordering is interpolated — source cannot settle it.
    "SELECT * FROM contacts ORDER BY {order_by} LIMIT %s OFFSET %s",
    "SELECT * FROM contacts ORDER BY {_contact_order_by(sort)} LIMIT %s",
    # The ORDER BY lives in a different string; this one only carries the cap.
    "SELECT id FROM deals {where} {order} LIMIT %s",
])
def test_scanner_reports_runtime_assembled_orders_as_unknown(sql):
    """`unknown` must stay distinct from `ok` — collapsing them is exactly how a reader
    could opt out of the guard by moving its ORDER BY into a variable."""
    state, detail = judge(sql)
    assert state == "unknown", f"scanner returned {state!r} ({detail}) for: {sql}"


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


# Every package that holds at least one capped reader. Asserted PER MODULE, not as one
# total: `crm` alone contributes well over half the statements, so a single total would
# stay satisfied while a narrowed walk quietly stopped scanning all the others.
MODULES_WITH_CAPPED_READERS = frozenset({
    "alerts", "assistant", "context_files", "core", "crm",
    "gmail_scan", "memory", "notifications", "proactive", "reminders",
})


def test_scan_actually_examined_the_backend():
    """Fail if the sweep stopped reaching part of the backend. Guards against a path or
    parse change turning the check below into a vacuous pass — the scanner reporting
    zero failures because it looked at nothing is indistinguishable from a clean run."""
    failures, examined, _ = _scan_backend()
    assert not [f for f in failures if "could not be scanned" in f], failures
    missed = MODULES_WITH_CAPPED_READERS - set(examined)
    assert not missed, (
        f"the sweep found no capped ORDER BY in {sorted(missed)} — either the scanner "
        "stopped reaching those packages, or their readers moved and this set needs "
        "updating. Do not delete an entry to make this pass without checking which.")


# Capped readers whose ordering is assembled at runtime, so source alone cannot settle
# them. ONLY these are registered, and each owes a behavioral test on the SQL it really
# emits — that pairing is the whole reason `unknown` is not simply waved through:
#
#   crm/service.py::list_contacts    ORDER BY {order_by}, from _CONTACT_SORTS
#   crm/service.py::search_contacts  same fragment
#                                      -> test_dynamic_order_by_contact_lists
#   crm/scoring_service.py::backfill_scores
#                                    two reads shaped `{where} {order} LIMIT %s`, whose
#                                    ORDER BY lives in a separate local
#                                      -> test_scoring_backfill_orders_are_total
#
# Other readers interpolate too but stay DECIDABLE, so they are deliberately absent:
# `list_companies` and `search_deals` end on a literal `id` term after the interpolated
# column, and `_stale_ids` is one implicitly-concatenated literal ending on `id` — the
# scan judges all three directly. They keep behavioral tests anyway, as belt and braces
# against the fragment changing shape. (`list_tasks` joins the decidable group when #77
# turns its ORDER BY into a `_TASK_SORTS` lookup; that constant is checked by
# test_order_by_fragment_constants_are_total.)
#
# A to-do list, not an exemption list — and pinned EXACTLY, both directions. Growth means
# a reader started hiding its ordering from the scan and owes a behavioral test first.
# Shrinkage means one stopped, and leaving a stale name here would quietly re-admit it
# later. (`crm/gtd_service.py` sat here until the scan proved its ORDER BYs are literal
# and already end on `t.id` — which is why this side is asserted too.)
#
# Keyed by enclosing FUNCTION, not by file: `crm/service.py` holds two undecidable
# readers, and a file-level count would net out to no change if one became decidable
# while a different one appeared — letting the newcomer inherit a registration that was
# never about it.
UNDECIDABLE_SITES = {
    "crm/service.py::list_contacts": 1,
    "crm/service.py::search_contacts": 1,
    "crm/scoring_service.py::backfill_scores": 2,  # the deals read and the contacts read
}


def test_undecidable_sites_are_registered():
    """A reader must not escape the guard by moving its ORDER BY into a variable.

    Before this check, `judge` returning "unknown" was indistinguishable from "ok", so
    `sql = f"... ORDER BY {order} LIMIT %s"` silently opted out of the sweep entirely.

    Known boundary: a reader that assembles its statement across SEVERAL literals
    (``sql = "SELECT …"``; ``sql += f" ORDER BY {o}"``; ``sql += " LIMIT %s"``) produces
    no literal carrying both keywords and so is not seen here at all. Following that
    would take dataflow analysis — a static analyser, not a test. The behavioral tests
    are the backstop for readers in the files above.
    """
    _, _, undecidable = _scan_backend()
    assert undecidable == UNDECIDABLE_SITES, (
        f"undecidable capped readers changed.\n  found:      {dict(sorted(undecidable.items()))}"
        f"\n  registered: {dict(sorted(UNDECIDABLE_SITES.items()))}\n\n"
        "A NEW or extra entry means a capped reader now assembles its ORDER BY at "
        "runtime, where the source scan cannot judge it: add a behavioral test "
        "asserting the SQL it emits (see test_dynamic_order_by_contact_lists), then "
        "update the count here. A MISSING or smaller entry means one stopped — drop it, "
        "so a later reader in that file cannot inherit the registration and slip past "
        "the sweep.")


# ── the sweep ────────────────────────────────────────────────────────────────────

def test_capped_readers_have_a_total_order():
    failures, _, _ = _scan_backend()
    assert not failures, (
        "capped readers with a non-reproducible page order (issue #58):\n  "
        + "\n  ".join(failures))


# ── readers whose ORDER BY the scan cannot resolve ───────────────────────────────

class _Recorder:
    """Records the SQL passed to the pg helpers; returns nothing useful.

    Stores it RAW. Collapsing whitespace here would destroy the newline that ends a
    ``--`` comment, so the later comment strip would eat the rest of the statement —
    silently turning every reader whose SQL carries an explanatory comment above its
    ORDER BY (``get_pipeline`` among them) into a vacuous check that passes with the
    tiebreaker removed. ``_prepare`` does the normalization, in the right order.
    """

    def __init__(self):
        self.sql: list[str] = []

    def fetchall(self, sql, params=()):
        self.sql.append(sql)
        return []

    def fetchone(self, sql, params=()):
        self.sql.append(sql)
        return {"cnt": 0}

    def capped(self) -> list[str]:
        """Every emitted capped SELECT — deliberately NOT filtered to those that already
        have an ORDER BY. Filtering on ORDER BY would make a reader that LOST its
        ordering disappear from the check instead of failing it, and a sibling call in
        the same test would still satisfy the non-empty assertion below."""
        out = []
        for sql in self.sql:
            _, masked = _prepare(sql)
            if _is_capped(masked) and re.search(r"\bSELECT\b", masked, re.I):
                out.append(sql)
        return out


def _assert_recorded_orders_are_total(recorder: _Recorder):
    capped = recorder.capped()
    assert capped, "the reader emitted no capped SELECT to check"
    for sql in capped:
        # `judge`, not `order_by_verdict`: at runtime every placeholder is resolved, so
        # an "unknown" here would mean the scanner mis-read real SQL, not that the
        # question is undecidable — it must not pass as success.
        state, detail = judge(sql)
        assert state == "ok", f"{state}: {detail}\n  emitted SQL: {sql}"


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


def test_hardened_uncapped_readers_keep_their_tiebreaker(monkeypatch):
    """The readers this change hardened *ahead* of a cap still carry their id term.

    These sort by a non-unique column with no LIMIT, so nothing can dupe or vanish today
    and the source scan skips them by design. They were given a tiebreaker anyway so that
    adding a cap later cannot quietly reintroduce the bug — ``get_pipeline`` being the
    reader #59 will paginate. That promise is worth nothing unless something fails when
    the term is dropped, which is what this test is for: it appends a LIMIT to the
    emitted SQL and applies exactly the rule that will govern these readers the moment
    one is really added.

    The list is explicit rather than derived: it names the sites this change claims to
    have future-proofed, so removing one is a decision someone has to make here, in view
    of that promise, rather than a silent loss of coverage.
    """
    from crm import provenance_service, service
    from notifications import subscriptions

    readers = [
        ("crm.service.get_pipeline", service, lambda: service.get_pipeline()),
        ("crm.service.get_contact_detail", service, lambda: service.get_contact_detail(1)),
        ("crm.service.get_company_detail", service, lambda: service.get_company_detail(1)),
        ("crm.provenance_service.get_provenance", provenance_service,
         lambda: provenance_service.get_provenance("contact", 1)),
        ("notifications.subscriptions.list_subscriptions", subscriptions,
         lambda: subscriptions.list_subscriptions()),
    ]

    checked = 0
    for name, module, call in readers:
        r = _Recorder()
        monkeypatch.setattr(module, "pg_fetchall", r.fetchall)
        if hasattr(module, "pg_fetchone"):
            monkeypatch.setattr(module, "pg_fetchone", r.fetchone)
        call()
        ordered = []
        for sql in r.sql:
            _, masked = _prepare(sql)
            if re.search(r"\bORDER\s+BY\b", masked, re.I):
                ordered.append((sql, _is_capped(masked)))
        assert ordered, f"{name} emitted no ORDER BY at all"
        for sql, already_capped in ordered:
            # Uncapped statements are judged under the capped rule on purpose — see the
            # docstring — by giving them the cap they do not yet have. Only statements
            # that lack one: appending to a query that is ALREADY capped would invent a
            # second, orderless cap, which `judge` rightly refuses. The probe is appended
            # on a NEWLINE so it survives a trailing `--` comment in the source SQL.
            probe = sql if already_capped else sql + "\nLIMIT 1"
            state, detail = judge(probe)
            assert state == "ok", f"{name}: {state}: {detail}\n  emitted SQL: {sql}"
            checked += 1
    assert checked >= len(readers), "at least one ORDER BY per reader should be checked"


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

        # The company rollup is capped, not paged, so it cannot drop a row — what the
        # tiebreaker buys there is that the list renders the same way every time. Give
        # it a genuine tie to resolve (identical names, one shared created_at) and assert
        # the exact contract `ORDER BY name ASC, id ASC` promises. Note this states the
        # contract rather than falsifying it: with a handful of rows Postgres may return
        # insertion order by luck even with no tiebreaker, so the guarantee that a
        # dropped term FAILS comes from test_hardened_uncapped_readers_keep_their_
        # tiebreaker, not from here.
        cid = first[0]
        with postgres.get_connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO contacts (name, company_id) VALUES (%s, %s)",
                    [("Same Name", cid)] * 8,
                )
            conn.commit()
        rollup = [c["id"] for c in service.get_company_detail(cid)["contacts"]]
        assert len(rollup) == 8, f"fixture did not link 8 contacts to company {cid}"
        assert rollup == sorted(rollup), (
            f"contacts tied on name must come back in ascending id order, got {rollup}")
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
