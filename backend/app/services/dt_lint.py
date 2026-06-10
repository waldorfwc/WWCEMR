"""Lint guard: refuse to ship code that reintroduces datetime.utcnow().

The codebase's canonical "now" lives in app/utils/dt.py. After the bulk
migration off `datetime.utcnow()`, a startup check blocks any future
reintroduction on Cloud Run. Local dev gets only a log warning. The check
is AST-based so comments and docstrings mentioning `datetime.utcnow` are
not flagged. (Fable design review note 5.)
"""
from __future__ import annotations

import ast
import os
from pathlib import Path


# Files allowed to reference datetime.utcnow — only the helper itself.
_ALLOWLIST = {
    "app/utils/dt.py",
}


def _is_utcnow_call(node: ast.AST) -> bool:
    """Return True for any expression of the form `datetime.utcnow`."""
    if isinstance(node, ast.Attribute) and node.attr == "utcnow":
        if isinstance(node.value, ast.Name) and node.value.id == "datetime":
            return True
    return False


def find_utcnow_hits() -> list[tuple[str, int]]:
    """Walk every app/**/*.py and return (relative_path, lineno) hits.

    Walks via AST so a 'datetime.utcnow' substring in a comment or
    docstring is ignored.
    """
    root = Path(__file__).resolve().parents[2]  # backend/
    hits: list[tuple[str, int]] = []
    for p in (root / "app").rglob("*.py"):
        rel = p.relative_to(root).as_posix()
        if rel in _ALLOWLIST:
            continue
        try:
            tree = ast.parse(p.read_text(), filename=str(p))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if _is_utcnow_call(node):
                hits.append((rel, getattr(node, "lineno", 0)))
    return hits
