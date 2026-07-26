"""Regression tripwire: provider SDKs must NEVER be imported at module top level
(only lazily inside methods, or under `if TYPE_CHECKING:`) so a missing SDK or
absent key can't break import/startup. AST-based, so it also catches
`from openai import AsyncOpenAI` — not just `import openai`."""

import ast
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
# Packages whose modules must keep provider/integration SDKs out of module scope.
SCANNED_DIRS = [_BACKEND / "providers", _BACKEND / "gmail"]
SDK_ROOTS = {"anthropic", "openai", "google", "googleapiclient", "httpx"}


def _top_level_import_nodes(tree: ast.Module):
    """Direct children of the module only. Imports nested in a FunctionDef (lazy)
    or an `if TYPE_CHECKING:` block are NOT in tree.body, so they're excluded."""
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node


def test_no_top_level_sdk_imports():
    offenders = []
    for scanned in SCANNED_DIRS:
        for path in sorted(scanned.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in _top_level_import_nodes(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                else:  # ImportFrom
                    names = [node.module or ""]
                for name in names:
                    if name.split(".")[0] in SDK_ROOTS:
                        offenders.append(f"{scanned.name}/{path.name}:{node.lineno} imports {name!r}")
    assert not offenders, (
        "Provider/integration SDKs must be imported lazily, not at module level:\n"
        + "\n".join(offenders)
    )
