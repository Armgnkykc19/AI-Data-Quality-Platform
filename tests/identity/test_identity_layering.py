"""Dependency direction for the identity package, asserted rather than assumed.

``identity`` is the most foundational package Sprint 13 adds: everything above
it may depend on it, and it may depend on nothing above it. Two consequences
matter enough to pin.

**Sprint 08 must stay ignorant of tenancy.** ``human_review`` answers whether a
MATCH is safe according to review evidence. If it could see a role or a
membership, a role could eventually overrule that judgement, which is the one
thing the whole human-review design exists to prevent. The two packages are
kept unaware of each other in both directions.

**Identity must stay framework-free.** No Pydantic, no FastAPI, no ``sqlite3``.
A domain model that knows how it is transported or stored cannot be reused by a
second transport or store, and cannot be built in a test without one.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
IDENTITY_PACKAGE = Path("identity")

# Nothing in identity may import any of these. The review packages are above
# it; the frameworks are outside it.
FORBIDDEN_IMPORTS = (
    "review_api",
    "review_application",
    "review_persistence",
    "human_review",
    "semantic_review",
    "entity_resolution",
    "survivorship",
    "fastapi",
    "pydantic",
    "starlette",
    "sqlite3",
    "yaml",
)

# Packages that must not learn about identity. The first three are the Sprint
# 08 domain and its neighbours: whether a merge is safe is a question about
# evidence, and it must stay answerable without knowing who is asking.
TENANCY_FREE_PACKAGES = (
    "human_review",
    "entity_resolution",
    "semantic_review",
    "survivorship",
    "dataset",
    "evaluation",
)


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def identity_modules() -> list[Path]:
    return sorted(IDENTITY_PACKAGE.rglob("*.py"))


def test_the_identity_package_was_found() -> None:
    """Guards every negative assertion below from passing on an empty list."""
    assert len(identity_modules()) >= 4


@pytest.mark.parametrize("module", FORBIDDEN_IMPORTS)
def test_identity_imports_nothing_above_or_outside_itself(module: str) -> None:
    offenders = [
        f"{path}: {imported}"
        for path in identity_modules()
        for imported in imported_modules(path)
        if imported == module or imported.startswith(f"{module}.")
    ]
    assert offenders == []


@pytest.mark.parametrize("package", TENANCY_FREE_PACKAGES)
def test_the_domain_packages_never_learn_about_identity(package: str) -> None:
    offenders = [
        f"{path}: {imported}"
        for path in sorted(Path(package).rglob("*.py"))
        for imported in imported_modules(path)
        if imported.split(".")[0] == "identity"
    ]
    assert offenders == []


def test_the_review_application_layer_may_depend_on_identity() -> None:
    """The allowed direction, pinned so a future refactor cannot invert it.

    ``review_application.queues`` references an organization and validates its
    identifier, so it imports from ``identity``. The reverse would mean the
    tenant graph knew what a review case is.
    """
    upward = {
        imported
        for path in sorted(Path("review_application").rglob("*.py"))
        for imported in imported_modules(path)
        if imported.split(".")[0] == "identity"
    }

    assert upward


@pytest.mark.parametrize("module", ["identity", "identity.models", "identity.ids"])
def test_identity_opens_no_database_and_no_socket(module: str, tmp_path: Path) -> None:
    """Importing the package must do nothing but define things.

    Run in a fresh interpreter rather than by reloading here. Deleting a
    package from ``sys.modules`` and re-importing it mid-session produces a
    second copy of every class in it, and an ``except`` clause written against
    one copy then fails to catch the other -- a failure mode that shows up in
    an unrelated test file and is very hard to trace back.
    """
    program = "\n".join(
        (
            f"import sys; sys.path.insert(0, {str(PROJECT_ROOT)!r})",
            "import socket, sqlite3",
            "def _forbidden(*a, **k):",
            "    raise AssertionError('identity touched storage or the network at import')",
            "sqlite3.connect = _forbidden",
            "socket.socket = _forbidden",
            f"import {module}",
            "print('OK')",
        )
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"
    # Run from a scratch directory, so any created file would be visible here.
    assert list(tmp_path.iterdir()) == []
