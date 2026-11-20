"""Layer rules from PLAN.md 2."""

from __future__ import annotations

import ast
import sys

from conftest import REPO


def imported_modules(path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0] if node.module.split(".")[0] != "dbc2h" else node.module)
    return names


def test_core_uses_only_the_standard_library_and_core():
    for path in (REPO / "dbc2h" / "core").rglob("*.py"):
        for name in imported_modules(path):
            if name.startswith("dbc2h"):
                assert name.startswith("dbc2h.core"), f"{path.name} importiert {name}"
            else:
                assert name in sys.stdlib_module_names or name == "__future__", f"{path.name} importiert {name}"
