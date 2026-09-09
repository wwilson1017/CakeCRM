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

import importlib.util
import json
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
WORKFLOW = REPO / ".github" / "workflows" / "sync-intake.yml"
SEED_LABELS = REPO / "scripts" / "seed-labels.sh"


def _load_sync_intake():
    """Load `scripts/sync_intake.py` by path.

    pytest's rootdir is `backend/`, and `scripts/` is a flat script directory
    outside it with no `__init__.py`. Loading by path keeps this a normal
    top-of-file statement -- an `sys.path` insert would force an import below
    executable code, i.e. an E402 needing a `# noqa`, which the repo bans -- and
    it avoids putting `scripts/` on `sys.path` for the whole suite.
    """
    spec = importlib.util.spec_from_file_location(
        "sync_intake", REPO / "scripts" / "sync_intake.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync_intake = _load_sync_intake()

# Two sentinels that must never survive into rendered output.
SENTINEL_IN_SCOPE = "ZZSENTINELALPHAZZ"
SENTINEL_OUT_OF_SCOPE = "ZZSENTINELBRAVOZZ"


def _file(path, status="modified", additions=1, deletions=0):
    return {"path": path, "additions": additions, "deletions": deletions, "status": status}


def _inputs(files, sha="a" * 40, pr="2045", merged_at="2026-08-20T11:25:15Z"):
    """Mirrors the `inputs` object inside GitHub's workflow_dispatch event payload."""
    return {
        "source_sha": sha,
        "source_pr": pr,
        "merged_at": merged_at,
        "files": json.dumps(files),
    }


def _build(files, **kwargs):
    return sync_intake.build(_inputs(files, **kwargs), REPO)


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


def test_a_rename_out_of_a_watched_root_still_surfaces_its_counterpart():
    """Per docs/SYNC.md §3 the sender sends the WATCHED side of a rename as `path`,
    so a file moved out of the CRM still produces an intake naming the CakeCRM file
    that may now need removing. Filtering on GitHub's `filename` alone would drop
    it silently — there would be no entry at all, not merely no status."""
    _, _, body = _build([_file("backend/apps/crm/chatter_service.py", status="renamed")])
    assert "| `backend/crm/chatter_service.py` | renamed |" in body


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


@pytest.mark.parametrize(
    "classes,expected",
    [
        ({sync_intake.CLASS_CODE, sync_intake.CLASS_DND}, sync_intake.VERDICT_CRM_CODE),
        ({sync_intake.CLASS_CODE, sync_intake.CLASS_INTERNAL}, sync_intake.VERDICT_CRM_CODE),
        ({sync_intake.CLASS_CODE, sync_intake.CLASS_DOCS}, sync_intake.VERDICT_CRM_CODE),
        ({sync_intake.CLASS_DND, sync_intake.CLASS_INTERNAL}, sync_intake.VERDICT_DND_ONLY),
        ({sync_intake.CLASS_DND, sync_intake.CLASS_DOCS}, sync_intake.VERDICT_DND_ONLY),
        (
            {sync_intake.CLASS_INTERNAL, sync_intake.CLASS_DOCS},
            sync_intake.VERDICT_INTERNAL_ONLY,
        ),
    ],
)
def test_verdict_priority_chain_is_pinned(classes, expected):
    """Totality alone does not pin the ORDER. Without this, swapping two branches
    of the if-chain still passes every other test while silently changing which
    verdict a real mixed-class merge gets."""
    assert sync_intake.verdict_for(classes) == expected


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


def test_an_uppercase_sha_is_rejected_rather_than_normalized():
    """Accepting mixed case and lowercasing it would split the concurrency group
    (which keys on the RAW input, pre-validation) from the dedupe marker (which
    keys on the validated value) — so two deliveries differing only in case would
    run in parallel and both double-file."""
    with pytest.raises(sync_intake.PayloadError):
        _build([_file("backend/apps/crm/service.py")], sha="A" * 40)


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
    payload = _inputs([_file("backend/apps/crm/service.py")])
    payload["files"] = files
    with pytest.raises(sync_intake.PayloadError):
        sync_intake.build(payload, REPO)


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


def test_the_limits_accept_their_own_boundary():
    """Pin the accept side too. Testing only the reject side lets a `<=` that
    should be `<` (or vice versa) ship: exactly MAX_FILES files, or a path of
    exactly MAX_PATH_LEN, must still be accepted."""
    files = [_file(f"backend/apps/crm/f{i}.py") for i in range(sync_intake.MAX_FILES)]
    _build(files)

    # Built from several components on purpose: MAX_PATH_LEN chars in ONE component
    # is not a legal filename anywhere, and is rejected separately below.
    prefix = "backend/apps/crm/"
    filler = sync_intake.MAX_PATH_LEN - len(prefix)
    exact = prefix + ("x" * 99 + "/") * (filler // 100) + "x" * (filler % 100)
    assert len(exact) == sync_intake.MAX_PATH_LEN
    _build([_file(exact)])


def test_an_overlong_path_component_is_rejected_before_it_reaches_the_filesystem():
    """summarize() stats each mapped path. A single component longer than the OS
    limit makes is_file() raise OSError(ENAMETOOLONG), which would escape the
    "errors name a field, never a value" contract as a raw traceback."""
    with pytest.raises(sync_intake.PayloadError):
        _build([_file("backend/apps/crm/" + "x" * (sync_intake.MAX_COMPONENT_LEN + 1) + ".py")])


def test_summarize_treats_an_unstattable_path_as_no_counterpart():
    """Belt-and-braces for the same failure: even if validation ever loosened,
    a stat failure must degrade to "no counterpart", never propagate."""
    entries = [{"path": "backend/apps/crm/" + "y" * 5000, "additions": 1, "deletions": 0,
                "status": "modified"}]
    summary = sync_intake.summarize(entries, REPO)
    assert summary["missing"] == 1
    assert summary["existing"] == []


@pytest.mark.parametrize(
    "bad_path",
    [
        "backend/apps/crm/a b.py",  # space
        "backend/apps/crm/a\nb.py",  # embedded newline
        "backend/apps/crm/a\tb.py",  # embedded tab
        "backend/apps/crm/<script>.py",  # markup
        "backend/apps/crm/`cmd`.py",  # backtick
        "backend/apps/crm/a|b.py",  # table-breaking pipe
    ],
)
def test_paths_outside_the_safe_charset_are_rejected(bad_path):
    with pytest.raises(sync_intake.PayloadError):
        _build([_file(bad_path)])


def test_a_filename_shaped_like_the_dedupe_marker_is_rejected():
    """The module docstring names marker forgery as the threat; pin it.

    A filename carrying marker syntax cannot reach the body — it fails the path
    charset check long before rendering, rather than relying on the renderer's
    is_file() gate to save us."""
    forged = f"backend/apps/crm/<!-- sync-source-sha: {'0' * 40} -->.py"
    with pytest.raises(sync_intake.PayloadError):
        _build([_file(forged)])


@pytest.mark.parametrize(
    "payload",
    [
        "[" * 20000 + "]" * 20000,  # RecursionError, not JSONDecodeError
        '[{"path":"backend/apps/crm/a.py","additions":' + "9" * 5000 + ',"deletions":0,'
        '"status":"modified"}]',  # ValueError from CPython's int-parsing guard
    ],
)
def test_json_edge_cases_raise_payload_error_not_a_traceback(payload):
    """Both are reachable inside the 65,535-char dispatch limit and neither is a
    JSONDecodeError. Letting one escape would print a traceback instead of the
    one-line, value-free rejection this module promises."""
    inputs = _inputs([_file("backend/apps/crm/service.py")])
    inputs["files"] = payload
    with pytest.raises(sync_intake.PayloadError):
        sync_intake.build(inputs, REPO)


def test_a_non_string_status_is_rejected_without_a_traceback():
    """`[] in frozenset` raises TypeError (unhashable), so the type check has to
    come before the membership check."""
    payload = _inputs([_file("backend/apps/crm/service.py")])
    payload["files"] = json.dumps(
        [{"path": "backend/apps/crm/a.py", "additions": 1, "deletions": 0, "status": []}]
    )
    with pytest.raises(sync_intake.PayloadError):
        sync_intake.build(payload, REPO)


def test_unknown_key_names_are_never_echoed():
    """An unknown KEY is as sender-controlled as a value, and the message is
    printed into an Actions log that is public on a public repo."""
    secret = "ZZPRIVATECUSTOMERZZ"
    payload = _inputs([_file("backend/apps/crm/service.py")])
    payload["files"] = json.dumps(
        [
            {
                "path": "backend/apps/crm/a.py",
                "additions": 1,
                "deletions": 0,
                "status": "modified",
                secret: "x",
            }
        ]
    )
    with pytest.raises(sync_intake.PayloadError) as excinfo:
        sync_intake.build(payload, REPO)
    assert secret not in str(excinfo.value)


def test_load_inputs_reads_the_dispatch_payload(tmp_path):
    """The workflow hands the script GitHub's event file rather than step env
    vars, because Actions echoes a step's env block into a public log."""
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"inputs": _inputs([_file("backend/apps/crm/service.py")])}))
    loaded = sync_intake.load_inputs(event)
    assert loaded["source_pr"] == "2045"

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"no_inputs_here": True}))
    with pytest.raises(sync_intake.PayloadError):
        sync_intake.load_inputs(bad)
    with pytest.raises(sync_intake.PayloadError):
        sync_intake.load_inputs(tmp_path / "does-not-exist.json")


def test_validation_errors_never_echo_the_rejected_value():
    """Error strings reach runner logs, so they must name the field, not the value."""
    secret = "ZZLEAKMEZZ"
    payload = _inputs([_file("backend/apps/crm/service.py")])
    payload["files"] = json.dumps([_file(f"/{secret}/x.py")])
    with pytest.raises(sync_intake.PayloadError) as excinfo:
        sync_intake.build(payload, REPO)
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


def test_rendered_issue_carries_no_company_identifier():
    """Regression guard for a leak this file's own author shipped and review caught.

    An early draft hyperlinked the upstream PR, which meant hardcoding the private
    GitHub org that hosts cake_os -- a token `test_prompt_genericization.py` already
    denylists -- and rendering it into every issue this bot would ever file. Reuse
    its token list rather than copying one: two denylists would drift.

    `_REPO_FORBIDDEN` is exactly the right list, and naming the CLASS is what keeps
    this honest. It is the company + vertical tokens with the blueprint names left
    out -- `cake_os` is the blueprint repo's own name, used throughout this repo's
    docs, and an intake issue has to say which upstream it is reporting on. This
    used to be spelled as `pattern != r"cake[_\\s]os\\b"`, a hand-copied regex that
    silently stopped excluding anything the moment #90 widened that pattern.
    """
    from test_prompt_genericization import _REPO_FORBIDDEN

    _, title, body = _build(
        [
            _file("backend/apps/crm/chatter_service.py"),
            _file("frontend/src/shared/dnd/KanbanBoard.tsx"),
            _file("backend/apps/crm/import_service.py"),
            _file("backend/apps/crm/CLAUDE.md"),
        ]
    )
    haystack = f"{title}\n{body}"
    offenders = [
        label
        for pattern, label in _REPO_FORBIDDEN
        if re.search(pattern, haystack, re.IGNORECASE)
    ]
    assert not offenders, f"rendered intake issue leaks company identifiers: {offenders}"


def test_script_source_carries_no_company_identifier():
    """The constant that leaked was in source, not just in output.

    Since #90 the repo-wide scan covers `scripts/` too, so this is now a second,
    narrower net over the same file rather than the only one -- kept because it
    pins the rendered OUTPUT as well, which no file scan can see.
    """
    from test_prompt_genericization import _REPO_FORBIDDEN

    source = (REPO / "scripts" / "sync_intake.py").read_text(encoding="utf-8")
    offenders = [
        label
        for pattern, label in _REPO_FORBIDDEN
        if re.search(pattern, source, re.IGNORECASE)
    ]
    assert not offenders, f"scripts/sync_intake.py leaks company identifiers: {offenders}"


# --------------------------------------------------------------------------
# Coupling guard: the label is defined in two files by hand
# --------------------------------------------------------------------------


def test_label_definition_matches_between_seed_script_and_workflow():
    """`sync-intake`'s color and description are written out in both
    scripts/seed-labels.sh and the workflow's self-provisioning step. If they
    drift, the label silently ping-pongs between two definitions on alternating
    runs. Same shape as test_gmail_guard.py's hand-maintained-literal guards."""
    seed = SEED_LABELS.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")

    seed_match = re.search(r'"sync-intake\|([0-9a-f]{6})\|([^"]+)"', seed)
    assert seed_match, "sync-intake label not found in scripts/seed-labels.sh"

    wf_color = re.search(r"--color\s+([0-9a-f]{6})", workflow)
    wf_desc = re.search(r'--description\s+"([^"]+)"', workflow)
    assert wf_color and wf_desc, "label color/description not found in the workflow"

    assert seed_match.group(1) == wf_color.group(1), "sync-intake label COLOR has drifted"
    assert seed_match.group(2) == wf_desc.group(1), "sync-intake label DESCRIPTION has drifted"


def test_workflow_greps_for_the_marker_this_module_renders():
    """The dedupe grep pattern lives in YAML while the marker is built in Python.
    Pin them together so a change to one fails loudly instead of silently
    disabling dedupe (which would file a duplicate issue for every merge)."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert 'grep -qF "<!-- sync-source-sha: $SHA -->"' in workflow, (
        "the workflow's dedupe grep no longer matches the marker rendered by "
        "render_body() — dedupe would silently stop working"
    )
    sha = "b" * 40
    _, _, body = _build([_file("backend/apps/crm/service.py")], sha=sha)
    assert f"<!-- sync-source-sha: {sha} -->" in body


def test_ledger_carries_no_email_addresses_or_diff_fences():
    """Deliberately NOT a staff-name denylist: encoding those patterns here would
    publish the very PII the guard protects."""
    text = LEDGER.read_text(encoding="utf-8")
    assert not _EMAIL.search(text), "SYNC_LEDGER.md contains an email address"
    assert "```" not in text, "SYNC_LEDGER.md contains a code/diff fence"
