"""Regression tripwire: provider SDKs must NEVER be imported at module top level
(only lazily inside methods, or under `if TYPE_CHECKING:`) so a missing SDK or
absent key can't break import/startup. AST-based, so it also catches
`from openai import AsyncOpenAI` — not just `import openai`."""

import ast
from pathlib import Path

PROVIDERS_DIR = Path(__file__).resolve().parent.parent / "providers"
SDK_ROOTS = {"anthropic", "openai", "google", "httpx"}


def _top_level_import_nodes(tree: ast.Module):
    """Direct children of the module only. Imports nested in a FunctionDef (lazy)
    or an `if TYPE_CHECKING:` block are NOT in tree.body, so they're excluded."""
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node


def test_no_top_level_sdk_imports():
    offenders = []
    for path in sorted(PROVIDERS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in _top_level_import_nodes(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            else:  # ImportFrom
                names = [node.module or ""]
            for name in names:
                if name.split(".")[0] in SDK_ROOTS:
                    offenders.append(f"{path.name}:{node.lineno} imports {name!r}")
    assert not offenders, (
        "Provider SDKs must be imported lazily, not at module level:\n"
        + "\n".join(offenders)
    )
