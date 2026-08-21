#!/usr/bin/env python3
"""Render a cake_os -> CakeCRM sync-intake issue from merge METADATA.

This is the receiving half of the sync bot (issue #23). cake_os fires a
`workflow_dispatch` at CakeCRM carrying merge metadata only -- never diff text,
never a PR title or body -- and this script validates it, classifies the changed
paths, and renders the issue body. It is deliberately:

  * pure -- environment in, files out. No network, no `gh`, no GitHub API. The
    workflow does the talking, so every rule below is unit-testable offline.
  * stdlib only -- the workflow installs nothing.

THE DISCLOSURE RULE, which is the whole point of the design: no cake_os path is
ever written into the rendered output. A path is only *prefix*-constrained, so
everything after `backend/apps/crm/` is free text chosen by whoever named the
file upstream -- it can carry a customer or staff name, or forge the dedupe
marker. What gets rendered instead is CakeCRM's OWN counterpart path, and only
when that file already exists in this tree (so the name is already public here).
Everything else becomes a count. See docs/SYNC.md and SECURITY.md.
"""

# Annotations stay unevaluated so this runs on whatever `python3` a runner or a
# self-hoster happens to have (the `X | None` syntax would otherwise need 3.10+).
# The backend targets 3.12, but this script is deliberately dependency-free and
# version-tolerant -- it is repo tooling, not app code.
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# cake_os paths we care about, and where their CakeCRM counterparts live. Order
# matters only for readability; the prefixes are disjoint.
WATCHED_ROOTS: tuple[tuple[str, str], ...] = (
    ("backend/apps/crm/", "backend/crm/"),
    ("frontend/src/apps/crm/", "frontend/src/crm/"),
    ("frontend/src/shared/dnd/", "frontend/src/shared/dnd/"),
)

DND_ROOT = "frontend/src/shared/dnd/"

# Structurally internal-only: bound to systems CakeCRM does not and will not
# have. EXACT paths, not basename matches -- a CakeCRM file that happens to be
# called import_service.py must not be swept up. Verified against cake_os
# master: import_service.py is Odoo-bound; lead_import_service.py is generic CSV
# but imports apps.dimm and apps.todo_gtd. Keep in sync with docs/SYNC.md.
INTERNAL_ONLY_PATHS: frozenset[str] = frozenset(
    {
        "backend/apps/crm/import_service.py",
        "backend/apps/crm/lead_import_service.py",
        "backend/apps/crm/tools/lead_import_tools.py",
    }
)

# GitHub's own fixed vocabulary. Validating against it is what stops `status`
# from becoming a free-text field.
VALID_STATUSES: frozenset[str] = frozenset(
    {"added", "modified", "removed", "renamed", "copied", "changed", "unchanged"}
)

MAX_FILES = 500
MAX_PATH_LEN = 400
SHA_RE = re.compile(r"\A[0-9a-fA-F]{40}\Z")
PR_RE = re.compile(r"\A[1-9][0-9]{0,6}\Z")

CAKEOS_PR_URL = "https://github.com/tncheesecake/cake_os/pull/{pr}"

# Per-file classes, and the verdicts derived from them. The verdicts are
# deliberately FACTUAL rather than portability judgments: portability is not
# decidable from a path (an Odoo-only edit inside router.py looks like `code`; a
# generic fix inside import_service.py looks like `internal`). Calling a verdict
# "portable" would promise a judgment this script cannot make.
CLASS_CODE = "code"
CLASS_DND = "dnd"
CLASS_DOCS = "docs"
CLASS_INTERNAL = "internal"

VERDICT_CRM_CODE = "crm-code"
VERDICT_DND_ONLY = "shared-dnd-only"
VERDICT_INTERNAL_ONLY = "internal-paths-only"
VERDICT_DOCS_ONLY = "docs-only"
VERDICT_NONE = "no-watched-files"

VERDICT_BLURB = {
    VERDICT_CRM_CODE: "CRM code changed upstream — worth a look.",
    VERDICT_DND_ONLY: (
        "Only `shared/dnd/` changed. That module has 13 non-CRM consumers in cake_os "
        "(CRM is 1 of 14), so this is most likely platform work, not CRM work."
    ),
    VERDICT_INTERNAL_ONLY: (
        "Only known internal-only paths changed (Odoo / lead-import). Nothing to port — "
        "close unless you know otherwise."
    ),
    VERDICT_DOCS_ONLY: "Only Markdown changed under the watched paths. Nothing to port.",
    VERDICT_NONE: (
        "Nothing under the watched paths changed. Filed for the audit trail — close it."
    ),
}


class PayloadError(ValueError):
    """A payload failed validation.

    The message names the offending FIELD and never quotes its value: an error
    string ends up in runner logs, and echoing raw input there would reintroduce
    exactly the leak the metadata-only design exists to prevent.
    """


def _require(condition: bool, field: str, why: str) -> None:
    if not condition:
        raise PayloadError(f"{field}: {why}")


def normalize_sha(raw: str) -> str:
    _require(bool(SHA_RE.match(raw or "")), "source_sha", "must be exactly 40 hex characters")
    return raw.lower()


def normalize_pr(raw: str) -> int:
    _require(
        bool(PR_RE.match((raw or "").strip())),
        "source_pr",
        "must be a positive integer with no leading zeros",
    )
    return int(raw.strip())


def normalize_merged_at(raw: str) -> str:
    """Parse a UTC ISO-8601 timestamp and re-emit it canonically.

    Canonicalizing (rather than passing the string through) means the rendered
    body carries a shape this script chose, not one the sender did.
    """
    text = (raw or "").strip()
    _require(bool(text), "merged_at", "is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PayloadError("merged_at: must be an ISO-8601 timestamp") from exc
    _require(parsed.tzinfo is not None, "merged_at", "must carry a UTC offset")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_path(path: str, field: str) -> str:
    _require(isinstance(path, str) and bool(path), field, "must be a non-empty string")
    _require(len(path) <= MAX_PATH_LEN, field, f"exceeds {MAX_PATH_LEN} characters")
    _require(not path.startswith("/"), field, "must be relative")
    _require(".." not in path.split("/"), field, "must not contain a '..' segment")
    _require("\\" not in path, field, "must use forward slashes")
    _require(path == path.strip(), field, "must not have leading or trailing whitespace")
    return path


def _validate_count(value: object, field: str) -> int:
    # `type(...) is int` deliberately, NOT isinstance: bool subclasses int, so
    # isinstance(True, int) is True and a boolean would sail through as a count.
    _require(type(value) is int, field, "must be an integer")
    _require(value >= 0, field, "must not be negative")
    return value


def parse_files(raw: str) -> list[dict]:
    """Validate the `files` input and return normalized entries."""
    try:
        data = json.loads(raw or "")
    except json.JSONDecodeError as exc:
        raise PayloadError("files: must be valid JSON") from exc

    _require(isinstance(data, list), "files", "must be a JSON array")
    _require(len(data) > 0, "files", "must not be empty")
    _require(len(data) <= MAX_FILES, "files", f"must not exceed {MAX_FILES} entries")

    allowed = {"path", "additions", "deletions", "status"}
    entries: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(data):
        field = f"files[{index}]"
        _require(isinstance(item, dict), field, "must be an object")
        unknown = set(item) - allowed
        _require(not unknown, field, f"has unknown key(s): {sorted(unknown)}")
        _require(allowed <= set(item), field, f"is missing key(s): {sorted(allowed - set(item))}")

        path = _validate_path(item["path"], f"{field}.path")
        _require(path not in seen, f"{field}.path", "is a duplicate")
        seen.add(path)

        status = item["status"]
        _require(status in VALID_STATUSES, f"{field}.status", "is not a recognized GitHub status")

        entries.append(
            {
                "path": path,
                "additions": _validate_count(item["additions"], f"{field}.additions"),
                "deletions": _validate_count(item["deletions"], f"{field}.deletions"),
                "status": status,
            }
        )
    return entries


def classify(path: str) -> str | None:
    """Classify one cake_os path. `None` means out of scope — dropped entirely.

    First match wins, and the order is load-bearing: the internal-only check
    must precede the docs and dnd checks so an excluded file cannot be
    reclassified by its extension.
    """
    if not any(path.startswith(root) for root, _ in WATCHED_ROOTS):
        return None
    if path in INTERNAL_ONLY_PATHS:
        return CLASS_INTERNAL
    if path.endswith(".md"):
        return CLASS_DOCS
    if path.startswith(DND_ROOT):
        return CLASS_DND
    return CLASS_CODE


def verdict_for(classes: set[str]) -> str:
    """Total function: every possible class set maps to exactly one verdict.

    Ordered most- to least-actionable, so a mixed set resolves to the most
    interesting thing in it rather than falling through to a wrong 'only' label.
    """
    if CLASS_CODE in classes:
        return VERDICT_CRM_CODE
    if CLASS_DND in classes:
        return VERDICT_DND_ONLY
    if CLASS_INTERNAL in classes:
        return VERDICT_INTERNAL_ONLY
    if CLASS_DOCS in classes:
        return VERDICT_DOCS_ONLY
    return VERDICT_NONE


def counterpart(path: str) -> str | None:
    """Map a cake_os path to the CakeCRM path that would correspond to it.

    Returns a CakeCRM-relative path, or None if the path is out of scope. This
    is a naming correspondence, NOT a claim about where a port should land —
    cake_os's 20 CRM modules collapse into CakeCRM's single service.py in many
    cases.
    """
    for source_root, dest_root in WATCHED_ROOTS:
        if path.startswith(source_root):
            return dest_root + path[len(source_root) :]
    return None


def summarize(entries: list[dict], repo_root: Path) -> dict:
    """Classify the payload and resolve which counterparts actually exist here."""
    counts = {CLASS_CODE: 0, CLASS_DND: 0, CLASS_DOCS: 0, CLASS_INTERNAL: 0}
    existing: list[tuple[str, str]] = []  # (CakeCRM path, status)
    missing = 0

    for entry in entries:
        klass = classify(entry["path"])
        if klass is None:
            continue  # out of scope: dropped here, never rendered, never logged
        counts[klass] += 1
        if klass == CLASS_INTERNAL:
            continue
        mapped = counterpart(entry["path"])
        # is_file(), not exists(): a directory sharing the name is not a counterpart.
        if mapped and (repo_root / mapped).is_file():
            existing.append((mapped, entry["status"]))
        else:
            missing += 1

    in_scope = sum(counts.values())
    return {
        "counts": counts,
        "in_scope": in_scope,
        "dropped": len(entries) - in_scope,
        "verdict": verdict_for({k for k, v in counts.items() if v}),
        "existing": sorted(set(existing)),
        "missing": missing,
    }


def render_title(pr: int, sha: str, summary: dict) -> str:
    return f"[Sync] cake_os PR #{pr} ({sha[:7]}) — {summary['verdict']}"


def render_body(pr: int, sha: str, merged_at: str, summary: dict) -> str:
    counts = summary["counts"]
    lines = [
        f"<!-- sync-source-sha: {sha} -->",
        "",
        "## cake_os → CakeCRM sync intake",
        "",
        f"**Verdict:** `{summary['verdict']}` — {VERDICT_BLURB[summary['verdict']]}",
        "",
        f"**Source:** cake_os PR [#{pr}]({CAKEOS_PR_URL.format(pr=pr)}) · "
        f"merged {merged_at} · `{sha[:7]}`",
        "",
        "| In-scope files | Count |",
        "|---|---|",
        f"| CRM code | {counts[CLASS_CODE]} |",
        f"| `shared/dnd/` | {counts[CLASS_DND]} |",
        f"| Markdown | {counts[CLASS_DOCS]} |",
        f"| Internal-only (excluded) | {counts[CLASS_INTERNAL]} |",
        f"| **Total in scope** | **{summary['in_scope']}** |",
        "",
        f"{summary['dropped']} changed file(s) outside the watched paths were dropped and "
        "are not listed — see the disclosure rule below.",
        "",
    ]

    if summary["existing"]:
        lines += [
            "### CakeCRM files with a same-named upstream counterpart",
            "",
            "These are **CakeCRM** paths that already exist in this repo, whose cake_os "
            "namesake changed in this merge. Treat them as candidates, not destinations — "
            "several cake_os modules fold into a single CakeCRM one.",
            "",
            "| CakeCRM path | Upstream status |",
            "|---|---|",
        ]
        lines += [f"| `{path}` | {status} |" for path, status in summary["existing"]]
        lines.append("")

    if summary["missing"]:
        lines += [
            f"Plus **{summary['missing']}** in-scope file(s) with no same-named CakeCRM "
            "counterpart (new upstream files, or ones that live under a different name here).",
            "",
        ]

    lines += [
        "### Next steps",
        "",
        "1. Open the cake_os PR above and decide whether the change is worth porting.",
        "2. If it is, label this issue `greenlit` and the normal `/auto-issues` pipeline "
        "ports it — reading cake_os source from the local clone, never from this issue.",
        "3. The port PR adds its own row to `SYNC_LEDGER.md`.",
        "4. If it isn't, just close this issue. It stays the dedupe anchor either way.",
        "",
        "Port playbook, classification rules, and the payload contract: `docs/SYNC.md`.",
        "",
        "---",
        "",
        "*Filed automatically from merge metadata by `scripts/sync_intake.py`. No cake_os "
        "file path, diff text, PR title, or PR body is carried in this issue — every path "
        "above is CakeCRM's own. Deterministic and keyless: no AI was involved.*",
    ]
    return "\n".join(lines)


def build(env: dict, repo_root: Path) -> tuple[str, str, str]:
    """Validate + render. Returns (normalized sha, title, body)."""
    sha = normalize_sha(env.get("SYNC_SOURCE_SHA", ""))
    pr = normalize_pr(env.get("SYNC_SOURCE_PR", ""))
    merged_at = normalize_merged_at(env.get("SYNC_MERGED_AT", ""))
    entries = parse_files(env.get("SYNC_FILES", ""))
    summary = summarize(entries, repo_root)
    return sha, render_title(pr, sha, summary), render_body(pr, sha, merged_at, summary)


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(
            "usage: sync_intake.py <repo-root> <title-out> <body-out>\n"
            "inputs are read from SYNC_SOURCE_SHA / SYNC_SOURCE_PR / SYNC_MERGED_AT / "
            "SYNC_FILES (env, not argv — untrusted values must never be interpolated "
            "into a shell command line)",
            file=sys.stderr,
        )
        return 2

    repo_root, title_out, body_out = Path(argv[1]), Path(argv[2]), Path(argv[3])
    try:
        sha, title, body = build(dict(os.environ), repo_root)
    except PayloadError as exc:
        # Safe to print: PayloadError messages name fields, never values.
        print(f"sync-intake payload rejected — {exc}", file=sys.stderr)
        return 1

    title_out.write_text(title + "\n", encoding="utf-8")
    body_out.write_text(body + "\n", encoding="utf-8")
    print(sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
