"""Enforcement + unit tests for the cake_os -> CakeCRM sync intake (issue #23).

The load-bearing test here is `test_no_upstream_path_reaches_the_rendered_body`.
The whole design rests on the claim that no cake_os text can land in a CakeCRM
artifact, and a path is only *prefix*-constrained -- everything after
`backend/apps/crm/` is free text chosen upstream. So the guarantee is asserted,
not argued. See docs/SYNC.md and SECURITY.md.

Note the fixtures use SYNTHETIC out-of-scope paths. Real ones would commit the
business-identifying upstream directory names into CakeCRM's permanent history
via this very file -- exactly the leak the feature exists to prevent.
"""

import json
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent

# Same idiom conftest.py uses for `backend/`: put the directory on sys.path and
# import the module bare. `scripts/` is a flat script dir, not a package, so
# there is no __init__.py to import through.
sys.path.insert(0, str(REPO / "scripts"))

import sync_intake  # noqa: E402  (import must follow the sys.path insert above)

# Two sentinels that must never survive into rendered output.
SENTINEL_IN_SCOPE = "ZZSENTINELALPHAZZ"
SENTINEL_OUT_OF_SCOPE = "ZZSENTINELBRAVOZZ"


def _file(path, status="modified", additions=1, deletions=0):
    return {"path": path, "additions": additions, "deletions": deletions, "status": status}


def _env(files, sha="a" * 40, pr="2045", merged_at="2026-08-20T11:25:15Z"):
    return {
        "SYNC_SOURCE_SHA": sha,
        "SYNC_SOURCE_PR": pr,
        "SYNC_MERGED_AT": merged_at,
        "SYNC_FILES": json.dumps(files),
    }


def _build(files, **kwargs):
    return sync_intake.build(_env(files, **kwargs), REPO)


# --------------------------------------------------------------------------
# The disclosure rule
# --------------------------------------------------------------------------


def test_no_upstream_path_reaches_the_rendered_body():
    """THE guarantee: nothing the sender named can appear in the issue."""
    _, title, body = _build(
        [
            _file(f"backend/apps/crm/{SENTINEL_IN_SCOPE}_service.py"),
            _file(f"frontend/src/apps/crm/{SENTINEL_IN_SCOPE}Tab.tsx"),
            _file(f"backend/apps/{SENTINEL_OUT_OF_SCOPE}/service.py"),
            _file(f"docs/{SENTINEL_OUT_OF_SCOPE}.md"),
        ]
    )
    for sentinel in (SENTINEL_IN_SCOPE, SENTINEL_OUT_OF_SCOPE):
        assert sentinel not in body, f"upstream filename leaked into the issue body: {sentinel}"
        assert sentinel not in title, f"upstream filename leaked into the issue title: {sentinel}"


def test_out_of_scope_files_are_dropped_not_counted_in_scope():
    _, _, body = _build(
        [
            _file("backend/apps/crm/service.py"),
            _file(f"backend/apps/{SENTINEL_OUT_OF_SCOPE}/a.py"),
            _file(f"backend/apps/{SENTINEL_OUT_OF_SCOPE}/b.py"),
        ]
    )
    assert "2 changed file(s) outside the watched paths were dropped" in body


def test_existing_cakecrm_counterpart_is_rendered_as_our_own_path():
    """A same-named file that exists HERE is safe to name — it is already public
    in this repo — and it is the actual signal first-look wants."""
    _, _, body = _build([_file("backend/apps/crm/chatter_service.py")])
    assert "`backend/crm/chatter_service.py`" in body
    assert "no same-named CakeCRM counterpart" not in body


def test_missing_counterpart_becomes_a_count_only():
    _, _, body = _build([_file(f"backend/apps/crm/{SENTINEL_IN_SCOPE}.py")])
    assert "**1** in-scope file(s) with no same-named CakeCRM counterpart" in body


def test_internal_only_files_get_no_counterpart_lookup():
    """Excluded files are reported as a count and never mapped — porting them is
    not on the table, so a destination hint would be misleading."""
    _, _, body = _build([_file("backend/apps/crm/import_service.py")])
    assert "| Internal-only (excluded) | 1 |" in body
    assert "### CakeCRM files with a same-named upstream counterpart" not in body
    assert "no same-named CakeCRM counterpart" not in body


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("backend/apps/crm/deal_service.py", sync_intake.CLASS_CODE),
        ("frontend/src/apps/crm/components/PipelineTab.tsx", sync_intake.CLASS_CODE),
        ("frontend/src/shared/dnd/collision.ts", sync_intake.CLASS_DND),
        ("backend/apps/crm/CLAUDE.md", sync_intake.CLASS_DOCS),
        ("backend/apps/crm/import_service.py", sync_intake.CLASS_INTERNAL),
        ("backend/apps/crm/lead_import_service.py", sync_intake.CLASS_INTERNAL),
        ("backend/apps/crm/tools/lead_import_tools.py", sync_intake.CLASS_INTERNAL),
        ("backend/main.py", None),
        ("docs/DRAG_DROP_GUIDE.md", None),
    ],
)
def test_classify(path, expected):
    assert sync_intake.classify(path) == expected


def test_internal_only_matches_exact_paths_not_basenames():
    """A CakeCRM-side file that merely shares a name must not be swept up."""
    assert sync_intake.classify("frontend/src/apps/crm/import_service.py") == sync_intake.CLASS_CODE


def test_verdict_is_a_total_function():
    """Every combination of classes -- including the empty set -- resolves."""
    classes = [
        sync_intake.CLASS_CODE,
        sync_intake.CLASS_DND,
        sync_intake.CLASS_DOCS,
        sync_intake.CLASS_INTERNAL,
    ]
    seen = set()
    for mask in range(1 << len(classes)):
        subset = {c for i, c in enumerate(classes) if mask & (1 << i)}
        verdict = sync_intake.verdict_for(subset)
        assert verdict in sync_intake.VERDICT_BLURB, f"{subset} produced an unknown verdict"
        seen.add(verdict)
    assert sync_intake.verdict_for(set()) == sync_intake.VERDICT_NONE
    assert seen == set(sync_intake.VERDICT_BLURB)


def test_counterpart_prefix_mapping():
    assert sync_intake.counterpart("backend/apps/crm/x.py") == "backend/crm/x.py"
    assert sync_intake.counterpart("frontend/src/apps/crm/X.tsx") == "frontend/src/crm/X.tsx"
    assert (
        sync_intake.counterpart("frontend/src/shared/dnd/x.ts") == "frontend/src/shared/dnd/x.ts"
    )
    assert sync_intake.counterpart("backend/main.py") is None


# --------------------------------------------------------------------------
# Verdicts against real merge shapes (paths only -- PII-safe)
# --------------------------------------------------------------------------


def test_dnd_only_merge_is_platform_scope_not_a_crm_signal():
    """cake_os's shared/dnd/ has 13 non-CRM consumers; CRM is 1 of 14. A dnd-only
    change is far more likely platform work, so it must not read as CRM work."""
    _, _, body = _build(
        [
            _file("frontend/src/shared/dnd/KanbanBoard.tsx"),
            _file("frontend/src/shared/dnd/collision.ts", status="added"),
            _file(f"frontend/src/apps/{SENTINEL_OUT_OF_SCOPE}/BoardPage.tsx"),
        ]
    )
    assert f"`{sync_intake.VERDICT_DND_ONLY}`" in body


def test_dnd_plus_crm_code_reads_as_crm_code():
    _, _, body = _build(
        [
            _file("frontend/src/shared/dnd/KanbanBoard.tsx"),
            _file("frontend/src/apps/crm/components/PipelineTab.tsx"),
        ]
    )
    assert f"`{sync_intake.VERDICT_CRM_CODE}`" in body


def test_docs_only_merge():
    """Real shape: one qualifying merge touched only backend/apps/crm/CLAUDE.md
    among ~40 unrelated files. Without a docs rule that implies portable work."""
    _, title, body = _build(
        [
            _file("backend/apps/crm/CLAUDE.md"),
            _file(f"backend/apps/{SENTINEL_OUT_OF_SCOPE}/payroll.py"),
        ]
    )
    assert f"`{sync_intake.VERDICT_DOCS_ONLY}`" in body
    assert sync_intake.VERDICT_DOCS_ONLY in title


def test_internal_only_merge():
    _, _, body = _build([_file("backend/apps/crm/import_service.py")])
    assert f"`{sync_intake.VERDICT_INTERNAL_ONLY}`" in body


def test_merge_with_nothing_in_scope():
    _, _, body = _build([_file(f"backend/apps/{SENTINEL_OUT_OF_SCOPE}/thing.py")])
    assert f"`{sync_intake.VERDICT_NONE}`" in body


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sha": "abc"},
        {"sha": "g" * 40},
        {"sha": ""},
        {"pr": "0"},
        {"pr": "007"},
        {"pr": "-1"},
        {"pr": "12345678"},
        {"pr": ""},
        {"merged_at": "not-a-date"},
        {"merged_at": "2026-08-20T11:25:15"},  # no offset
        {"merged_at": ""},
    ],
)
def test_scalar_validation_rejects(kwargs):
    with pytest.raises(sync_intake.PayloadError):
        _build([_file("backend/apps/crm/service.py")], **kwargs)


def test_sha_is_normalized_to_lowercase():
    sha, _, body = _build([_file("backend/apps/crm/service.py")], sha="A" * 40)
    assert sha == "a" * 40
    assert f"<!-- sync-source-sha: {'a' * 40} -->" in body


def test_timestamp_is_canonicalized():
    _, _, body = _build(
        [_file("backend/apps/crm/service.py")], merged_at="2026-08-20T06:25:15-05:00"
    )
    assert "2026-08-20T11:25:15Z" in body


@pytest.mark.parametrize(
    "files",
    [
        "not json",
        "{}",
        "[]",
        '[{"path": "backend/apps/crm/a.py"}]',  # missing keys
        '[{"path": "backend/apps/crm/a.py", "additions": 1, "deletions": 0, '
        '"status": "modified", "extra": 1}]',
        '[{"path": "/etc/passwd", "additions": 1, "deletions": 0, "status": "modified"}]',
        '[{"path": "../secrets.py", "additions": 1, "deletions": 0, "status": "modified"}]',
        '[{"path": "backend/apps/crm/a.py", "additions": 1, "deletions": 0, "status": "bogus"}]',
        '[{"path": "backend/apps/crm/a.py", "additions": -1, "deletions": 0, '
        '"status": "modified"}]',
        # bool must not pass as an int count
        '[{"path": "backend/apps/crm/a.py", "additions": true, "deletions": 0, '
        '"status": "modified"}]',
    ],
)
def test_files_validation_rejects(files):
    env = _env([_file("backend/apps/crm/service.py")])
    env["SYNC_FILES"] = files
    with pytest.raises(sync_intake.PayloadError):
        sync_intake.build(env, REPO)


def test_duplicate_paths_are_rejected():
    with pytest.raises(sync_intake.PayloadError):
        _build([_file("backend/apps/crm/a.py"), _file("backend/apps/crm/a.py")])


def test_too_many_files_are_rejected():
    files = [_file(f"backend/apps/crm/f{i}.py") for i in range(sync_intake.MAX_FILES + 1)]
    with pytest.raises(sync_intake.PayloadError):
        _build(files)


def test_overlong_path_is_rejected():
    with pytest.raises(sync_intake.PayloadError):
        _build([_file("backend/apps/crm/" + "x" * sync_intake.MAX_PATH_LEN + ".py")])


def test_validation_errors_never_echo_the_rejected_value():
    """Error strings reach runner logs, so they must name the field, not the value."""
    secret = "ZZLEAKMEZZ"
    env = _env([_file("backend/apps/crm/service.py")])
    env["SYNC_FILES"] = json.dumps([_file(f"/{secret}/x.py")])
    with pytest.raises(sync_intake.PayloadError) as excinfo:
        sync_intake.build(env, REPO)
    assert secret not in str(excinfo.value)
    assert "files[0].path" in str(excinfo.value)


# --------------------------------------------------------------------------
# Dedupe marker
# --------------------------------------------------------------------------


def test_marker_carries_the_full_sha_so_short_lookalikes_cannot_collide():
    sha = "b9ca60de051a20582416e2e01fb7acf76890923e"
    _, _, body = _build([_file("backend/apps/crm/service.py")], sha=sha)
    assert body.startswith(f"<!-- sync-source-sha: {sha} -->")
    assert f"<!-- sync-source-sha: {sha[:7]} -->" not in body


# --------------------------------------------------------------------------
# Ledger hygiene
# --------------------------------------------------------------------------

LEDGER = REPO / "SYNC_LEDGER.md"
_LEDGER_HEADER = "| Feature | Source | CakeCRM PR / issue | Date |"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _ledger_rows(text: str) -> list[list[str]]:
    rows, in_table = [], False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == _LEDGER_HEADER:
            in_table = True
            continue
        if in_table:
            if not stripped.startswith("|"):
                in_table = False
                continue
            if set(stripped) <= set("|- "):
                continue  # separator row
            rows.append([c.strip() for c in stripped.strip("|").split("|")])
    return rows


def test_ledger_parses_to_schema():
    rows = _ledger_rows(LEDGER.read_text(encoding="utf-8"))
    assert rows, "SYNC_LEDGER.md has no ledger rows — the pre-ledger seed is missing"
    for row in rows:
        assert len(row) == 4, f"ledger row does not have 4 columns: {row[0]!r}"
        assert row[2] == "seed" or re.fullmatch(r"#\d+", row[2]), (
            f"ledger row {row[0]!r} has a malformed CakeCRM reference: {row[2]!r}"
        )
        assert row[3] == "—" or re.fullmatch(r"\d{4}-\d{2}-\d{2}", row[3]), (
            f"ledger row {row[0]!r} has a malformed date: {row[3]!r}"
        )


def test_ledger_carries_no_email_addresses_or_diff_fences():
    """Deliberately NOT a staff-name denylist: encoding those patterns here would
    publish the very PII the guard protects."""
    text = LEDGER.read_text(encoding="utf-8")
    assert not _EMAIL.search(text), "SYNC_LEDGER.md contains an email address"
    assert "```" not in text, "SYNC_LEDGER.md contains a code/diff fence"
