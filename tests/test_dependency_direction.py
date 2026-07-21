"""Architectural boundary: `domain` and `application` must never import `infrastructure`.

Regression for an ST-02 review finding: `application/bootstrap.py` imported
`infrastructure.sqlite.seed`, reversing the approved dependency direction (domain/
application depend on nothing concrete; infrastructure depends on them). This test walks
the actual source of every module in those two packages so a future violation fails the
suite instead of waiting for the next review.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "personal_graph_os"


def _imported_module_names(python_file: Path) -> set[str]:
    tree = ast.parse(python_file.read_text(encoding="utf-8"), filename=str(python_file))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_domain_and_application_do_not_import_infrastructure() -> None:
    violations: list[str] = []
    for package in ("domain", "application"):
        for python_file in (_SRC_ROOT / package).rglob("*.py"):
            for module_name in _imported_module_names(python_file):
                if module_name.startswith("personal_graph_os.infrastructure"):
                    violations.append(f"{python_file.relative_to(_SRC_ROOT)} imports {module_name}")

    assert violations == [], "\n".join(violations)
