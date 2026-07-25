"""Tripwire: the issue-#6 packages must not import their heavy deps
(apscheduler/croniter/pywebpush/py_vapid) at MODULE TOP LEVEL — only lazily inside
functions — so the module graph imports cleanly without them installed. AST-based
(matches tests/test_lazy_imports.py's technique), plus a real import smoke test.

Note: apscheduler/croniter ARE required at startup (main.py's lifespan calls
start_scheduler), so this is about import-graph hygiene, not a "missing dep can't
break startup" guarantee — the scheduler genuinely needs them to run.
"""

import ast
import importlib
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
PKGS = ["reminders", "notifications", "alerts", "heartbeat"]
HEAVY_ROOTS = {"apscheduler", "croniter", "pywebpush", "py_vapid"}


def _top_level_imports(tree: ast.Module):
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node


def test_no_top_level_heavy_imports():
    offenders = []
    for pkg in PKGS:
        for path in sorted((BACKEND / pkg).glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in _top_level_imports(tree):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""])
                for name in names:
                    if name.split(".")[0] in HEAVY_ROOTS:
                        offenders.append(f"{pkg}/{path.name}:{node.lineno} imports {name!r}")
    assert not offenders, "Heavy deps must be imported lazily:\n" + "\n".join(offenders)


def test_modules_import_clean():
    # Every new module imports without a DB or the heavy deps being exercised.
    for mod in [
        "reminders.recurrence", "reminders.service", "reminders.tools", "reminders.router",
        "notifications.vapid", "notifications.subscriptions", "notifications.service",
        "notifications.delivery", "notifications.tools", "notifications.router",
        "alerts.service", "alerts.router",
        "heartbeat.service", "heartbeat.scheduler", "heartbeat.router",
        "assistant.background",
    ]:
        importlib.import_module(mod)
