---
title: Writing a repo-sweeping guard test that can actually fail
date: 2026-08-27
category: architecture-patterns
module: backend/tests
tags: [testing, static-analysis, guard-tests, falsifiability, sql, ast]
problem_type: pattern
---

## Context

CakeCRM has a growing family of **guard tests** — tests that sweep the whole tree and assert
a property rather than exercise one function: `test_route_authz` (every admin route is
gated), `test_gmail_guard` (no email-send surface exists), `test_prompt_genericization` (no
company tokens reach the model), and now `test_query_determinism` (every capped reader has a
total `ORDER BY`, issue #58).

These are high-leverage: one test defends an invariant across files nobody will remember to
check. They also share one catastrophic failure mode — **a sweep that quietly stops matching
is a permanent green**, and a permanent green is worse than no test because it reads as
coverage. Issue #58's guard hit that failure mode four separate times during review, in four
different ways, and every instance was caught by a reviewer rather than by the test suite.

This documents the shape that survived.

## Guidance

### 1. Pin the detector in every direction, on synthetic input

A sweep needs its own unit tests, feeding it strings it MUST flag and strings it MUST NOT:

```python
@pytest.mark.parametrize("sql", [
    "SELECT * FROM alerts ORDER BY created_at DESC LIMIT %s",     # must flag
    "SELECT * FROM contacts LIMIT 20",                            # no ordering at all
    "SELECT * FROM t\nORDER BY created_at DESC -- ORDER BY id\nLIMIT 10",
])
def test_scanner_rejects_a_non_total_capped_order(sql):
    assert judge(sql)[0] == "fail"
```

Every reviewer finding that exposed a false pass became a new parametrize entry. That is the
mechanism by which the guard gets *harder* over time instead of drifting.

### 2. Assert the sweep still reached the real code

`assert not failures` passes just as happily when the scan examined nothing. Assert coverage
**per module or per registered item — never one repo-wide total**:

```python
MODULES_WITH_CAPPED_READERS = frozenset({"alerts", "assistant", "crm", "memory", ...})

def test_scan_actually_examined_the_backend():
    _, examined, _ = _scan_backend()
    missed = MODULES_WITH_CAPPED_READERS - set(examined)
    assert not missed, f"the sweep found nothing in {sorted(missed)} ..."
```

A single total does not work: `crm/` alone contributed 30 of 49 matches, so a scan
accidentally narrowed to one package would still clear a threshold of 25.

### 3. Give "can't tell" its own state, and make it fail until registered

The first design returned `None` for both "provably fine" and "cannot determine". That let
any reader leave the guard by moving its `ORDER BY` into a variable:

```python
order = "created_at DESC"
sql = f"SELECT * FROM t ORDER BY {order} LIMIT %s"   # silently unscanned
```

Three states fix it. `unknown` sites are pinned in a registry **in both directions**:

```python
UNDECIDABLE_SITES = {
    "crm/service.py::list_contacts": 1,
    "crm/scoring_service.py::backfill_scores": 2,
}
```

Growth means a new site owes a behavioral test. Shrinkage means a stale entry would let a
future sibling in the same file inherit an exemption. Key by **enclosing function** — a
file-level count nets out to no change when one site becomes decidable as another appears,
and a line number churns on every edit above it.

This registry is a to-do list, not an allowlist: it fails when it *grows*.

### 4. Parse with `ast`, not line regexes

Python concatenates adjacent string literals at parse time, so a statement split across
source lines arrives as one node; an f-string arrives as a `JoinedStr` whose parts can be
stitched back with each interpolation rendered as `{expr}`. A line-oriented regex sees
neither. Skip bare string *expressions* — that is every docstring, several of which quote the
very invariant being checked.

### 5. Normalize in the right order, and mask rather than delete

Two ordering bugs, both producing silent passes:

- **Comments before whitespace.** A `--` comment ends at a newline. Flatten first and it
  swallows the rest of the statement. This made `get_pipeline`'s check vacuous.
- **Quote-aware stripping.** `WHERE marker = '--'` is data, not a comment; a naive strip eats
  its `LIMIT` and the query reads as uncapped.

For keyword searching, build a **length-preserving mask** (placeholder and quoted contents
blanked, same length) so offsets found in the mask still index the original for slicing:

```python
def _prepare(sql):
    flat = " ".join(_strip_sql_comments(sql).split())
    return flat, _mask_placeholders(_mask_quoted(flat))
```

Also require the literal to look like SQL. A case-insensitive `\bLIMIT\b` matched 25 English
prose strings in this backend (`"exceeds the 10 MB limit"`, the tool-schema key `"limit"`).

### 6. State the limits in the code, honestly

`judge()`'s docstring names what it cannot do — uniqueness inferred from a column's *name*,
so an alias or a row-multiplying join would pass; span-based scoping that can produce a false
*failure*; multi-literal assembly it cannot see at all — and separates the limits that could
pass a **broken** reader from the ones that are merely loud. Four separate inaccurate claims
in comments and CLAUDE.md were caught during this review; in a repo where automated workers
read the docs as instructions, an overclaim is a defect.

### 7. Back the shape with one test that reproduces the real bug

Everything above asserts *shape*. One integration test asserts *behavior*: insert rows inside
one transaction (so `now()` gives them a byte-identical timestamp), page through, assert no
duplicates and no gaps. With the tiebreaker removed it reports a duplicated row and a skipped
one on 25 rows — the actual user-visible bug, not an inference from SQL text.

### 8. More ways a passing mutation check lies (run 20260825, six more workers)

The same run that produced this doc hit six further failure modes of the falsification
discipline itself — each one a mutation check that *looked* performed and proved nothing:

- **The replica, not the artifact** (#102). A test asserting a migration's `WHERE` scoping
  executed a hand-typed copy of the statement. Mutating the copy went red — verified! —
  while deleting the `WHERE` clause from the real migration file left the suite green.
  `read_text()` the real file and execute it. Detector: the test names a file in its
  docstring but never opens it.
- **The sibling guard absorbed it** (#83). A test passed with the guard it named removed,
  because a neighbouring guard did the work first. The mutation must remove the *specific*
  guard the test names, and the test must fail *for that reason*.
- **The window had closed** (#83). A test asserting "the deferred replay did NOT take over
  the page" mocked the replayed request to fail instantly, so the takeover was over before
  the assertion ran — it passed against the exact P1 it named. Guards on TRANSIENT state
  must run inside the async window: hold the promise on a rejectable deferred and assert
  while it is in flight.
- **One guard, three call sites** (#83). Only 1 of 3 duplicated sites was covered; mutating
  either other one passed. The fix was not two more tests but collapsing the copies into one
  helper — prefer removing the duplication over adding N−1 tests.
- **Unfalsifiable on this runtime** (#74). A test for a Safari parsing fix stayed green with
  the fix reverted, because V8 parses the input happily. If the revert stays green, find the
  half of the behavior that IS observable on the runtime the test runs on, and label the
  rest as documentation, not a guard.
- **Right revert, wrong conclusion** (#96). Two tests survived a full revert of the fix —
  they were real guards against *over-extending* the new mechanism, not against the
  regression their names claimed. If a test survives the revert, rename it to what it
  actually guards (or delete it); never leave a name that reads as coverage of the bug.

Two more hygiene rules from the same run: **fold case** when asserting on SQL text — the
uppercase-keyword convention is a convention, not a constraint (#71); and a **fresh-DB test
cannot prove a data-repair migration** — the column default already satisfies the assertion,
so seed the pre-migration state explicitly and watch the inverted `WHERE` fail (#71, #102).

And before any of this: **commit the working tree first.** The natural undo for an injected
regression is `git checkout -- <file>`, which restores from INDEX/HEAD and silently destroys
uncommitted work in that file. Three separate workers lost real edits to it this run.

## Why This Matters

Every one of the four false passes in this guard was found by a reviewer, never by the
suite. The pattern above is what converts each of those findings into a permanent regression
test instead of a one-time fix.

The falsifiability discipline has a sharp edge worth naming: **assert which check failed, not
that the suite went red.** Removing one tiebreaker to prove `get_pipeline`'s check was live
turned the suite red — from a *sibling* reader the same edit had also matched, while
`get_pipeline`'s own check was vacuous. That false reassurance survived until a reviewer
reconstructed the mechanism two turns later.

And re-run the whole matrix against the FINAL commit. The scanner here was rewritten four
times during review, and each rewrite changed which previously-proven regressions still
failed.

## When to Apply

Any test that sweeps the tree and asserts a property: authz coverage, banned-API surfaces,
prompt hygiene, schema/lifecycle invariants, query determinism.

Do **not** apply this weight to a normal unit test. The machinery here (an AST walk, a
quote-aware lexer, two pinned registries) is justified by one thing only: the test's value
depends entirely on it still matching, and nothing else in the suite would notice if it
stopped.
