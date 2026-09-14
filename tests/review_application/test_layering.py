"""Dependency direction, asserted rather than assumed.

Sprint 10 is layered domain -> application -> persistence, and the arrows only
point one way. ``review_persistence`` may import ``review_application``
contracts and models; the reverse would mean the application layer knew how its
data is stored, and the first symptom is always a storage constant appearing in
a service.

These tests exist because that leak is invisible at runtime. Everything keeps
working right up until someone needs a second backend, or until a schema bump
silently changes what the application writes.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APPLICATION_PACKAGE = Path("review_application")
SERVICE_SOURCE = APPLICATION_PACKAGE / "service.py"
PERSISTENCE_PACKAGES = ("review_persistence",)

# Storage details the application layer must not name.
#
# The semantic_suggestions table is deliberately absent from this list: the
# repository Protocol legitimately declares record_semantic_suggestion and
# list_semantic_suggestions, which are application vocabulary that happens to
# contain the table's name. The tokens below have no such innocent spelling.
FORBIDDEN_IN_APPLICATION = (
    "DATABASE_SCHEMA_VERSION",
    "sqlite3",
    "review_cases",
    "review_case_events",
    "schema_meta",
    "PRAGMA",
    "BEGIN IMMEDIATE",
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


def application_modules() -> list[Path]:
    return sorted(APPLICATION_PACKAGE.rglob("*.py"))


def code_without_prose(path: Path) -> str:
    """Executable code only, with comments and docstrings stripped.

    The module docstrings legitimately explain what the persistence layer does;
    what matters is whether the code names any of it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if ast.get_docstring(node) is not None:
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


# --------------------------------------------------------------------------
# The application layer does not depend on persistence
# --------------------------------------------------------------------------


def test_the_service_imports_nothing_from_persistence() -> None:
    offenders = sorted(
        module
        for module in imported_modules(SERVICE_SOURCE)
        if module.split(".")[0] in (*PERSISTENCE_PACKAGES, "sqlite3")
    )
    assert offenders == []


def test_no_application_module_imports_persistence() -> None:
    """The whole package, not only the service.

    A single helper reaching downward would reintroduce the import cycle that
    made the clock move to review_application in the first place.
    """
    offenders = [
        f"{path}: {module}"
        for path in application_modules()
        for module in imported_modules(path)
        if module.split(".")[0] in (*PERSISTENCE_PACKAGES, "sqlite3")
    ]
    assert offenders == []


@pytest.mark.parametrize("token", FORBIDDEN_IN_APPLICATION)
def test_the_application_layer_names_no_storage_detail(token: str) -> None:
    offenders = [str(path) for path in application_modules() if token in code_without_prose(path)]
    assert offenders == []


def test_persistence_may_still_depend_on_the_application_layer() -> None:
    """The allowed direction, pinned so a future 'fix' does not invert it.

    Persistence implements the application's repository Protocol and stores its
    models, so it must import them. That is the dependency inversion working as
    intended, not a leak.
    """
    downward = {
        module
        for path in Path("review_persistence").rglob("*.py")
        for module in imported_modules(path)
        if module.split(".")[0] == "review_application"
    }
    assert "review_application.models" in downward
    assert "review_application.errors" in downward


# --------------------------------------------------------------------------
# No production dependency on a private Sprint 08 helper
# --------------------------------------------------------------------------


def test_no_sprint_10_production_module_uses_the_private_rebuild_helper() -> None:
    """Reconstruction goes through the public Sprint 08 seam.

    A production dependency on an underscore-prefixed helper is one refactor
    away from breaking, and this one rebuilds the AUTO_MATCH graph that MATCH
    authorization reads.
    """
    offenders = [
        str(path)
        for package in ("review_application", "review_persistence")
        for path in Path(package).rglob("*.py")
        if "_rebuild_resolution" in code_without_prose(path)
    ]
    assert offenders == []


def test_the_public_reconstruction_seam_exists_and_is_public() -> None:
    from human_review import reporting

    assert hasattr(reporting, "rebuild_resolution_from_snapshot")
    assert not hasattr(reporting, "_rebuild_resolution"), (
        "Two names for one reconstruction would let them drift apart."
    )
