"""Structural guarantees that advisory storage cannot grow into authority.

The behavioural tests show that today's code keeps suggestions advisory. These
show that the shapes which would end that are absent from the source: an UPDATE
against the advisory table, a case mutation on the semantic path, a service that
reads suggestions, or a dependency arrow pointing the wrong way.

They are cheap, and they fail at the moment someone writes the wrong line rather
than at the moment a reviewer's decision gets overwritten.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from review_persistence.schema import SEMANTIC_SUGGESTIONS_TABLE
from review_persistence.sqlite import review_repository, semantic_mapper

SERVICE_SOURCE = Path("review_application/service.py")
SEMANTIC_MAPPER_SOURCE = Path("review_persistence/sqlite/semantic_mapper.py")
SEMANTIC_REVIEW_PACKAGE = Path("semantic_review")


def code_without_prose(path: Path) -> str:
    """Executable code only: docstrings explain the rules, code obeys them."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if ast.get_docstring(node) is not None:
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def composed_sql(module: object) -> dict[str, str]:
    """Module-level SQL constants -- the text that actually reaches SQLite."""
    return {
        name: value.upper()
        for name, value in vars(module).items()
        if isinstance(value, str)
        and name.startswith("_")
        and not name.startswith("__")
        and any(verb in value.upper() for verb in ("SELECT", "INSERT", "UPDATE", "DELETE"))
    }


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


# --------------------------------------------------------------------------
# Advisory rows are immutable
# --------------------------------------------------------------------------


def semantic_statements() -> dict[str, str]:
    table = SEMANTIC_SUGGESTIONS_TABLE.upper()
    statements = {
        name: sql for name, sql in composed_sql(review_repository).items() if table in sql
    }
    assert statements, "Phase E must emit SQL against semantic_suggestions."
    return statements


@pytest.mark.parametrize(
    "forbidden", ["UPDATE ", "DELETE", "INSERT OR REPLACE", "INSERT OR IGNORE"]
)
def test_no_statement_can_rewrite_a_stored_suggestion(forbidden: str) -> None:
    """A recorded observation is what the adviser said, permanently.

    INSERT OR IGNORE is banned alongside the destructive verbs: it would make a
    conflicting duplicate look like a successful replay, which is precisely the
    contradiction the conflict error exists to surface.
    """
    for name, sql in semantic_statements().items():
        assert forbidden not in sql, f"{name}: {sql}"


def test_the_only_semantic_write_is_a_plain_insert() -> None:
    inserts = {name for name, sql in semantic_statements().items() if "INSERT" in sql}
    assert len(inserts) == 1, sorted(inserts)


def test_the_semantic_mapper_contains_no_sql() -> None:
    """Serialization decides what a row means, never how it is written."""
    code = code_without_prose(SEMANTIC_MAPPER_SOURCE).upper()
    for verb in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "COMMIT", "ROLLBACK", "SQLITE3"):
        assert verb not in code, verb


def test_the_semantic_mapper_holds_no_authorization_logic() -> None:
    code = code_without_prose(SEMANTIC_MAPPER_SOURCE)
    for token in (
        "assert_human_match_allowed",
        "assert_human_match_authorization_boundary",
        "projected_review_component_member_ids",
        "ReviewWorkflow",
        "ReviewStatus",
        "HumanReviewDecision",
    ):
        assert token not in code, token


# --------------------------------------------------------------------------
# The advisory path cannot reach Human Review state
# --------------------------------------------------------------------------


def test_the_semantic_methods_never_name_the_case_mutation_helper() -> None:
    """Read the two methods' own code and check what they call.

    ``record_semantic_suggestion`` reads review_cases to verify identity, which
    is legitimate. What it must never do is call the compare-and-swap that moves
    a case, or the resolution path built on it.
    """
    tree = ast.parse(Path("review_persistence/sqlite/review_repository.py").read_text("utf-8"))
    methods = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name in ("record_semantic_suggestion", "list_semantic_suggestions")
    }
    assert set(methods) == {"record_semantic_suggestion", "list_semantic_suggestions"}

    forbidden = {
        "_compare_and_swap_case",
        "apply_resolution",
        "_CAS_UPDATE_CASE",
        "with_case",
        "resolve_case",
    }
    for name, node in methods.items():
        called = {
            child.attr if isinstance(child, ast.Attribute) else child.id
            for child in ast.walk(node)
            if isinstance(child, ast.Attribute | ast.Name)
        }
        assert not called & forbidden, (name, sorted(called & forbidden))


def test_the_review_queue_service_never_reads_a_suggestion() -> None:
    """Human Review resolution must not consult advice, even to log it."""
    modules = imported_modules(SERVICE_SOURCE)
    assert not {module for module in modules if module.split(".")[0] == "semantic_review"}

    code = code_without_prose(SERVICE_SOURCE)
    for token in (
        "list_semantic_suggestions",
        "record_semantic_suggestion",
        "SemanticSuggestion",
        "semantic_suggestions",
    ):
        assert token not in code, token


# --------------------------------------------------------------------------
# Dependency direction
# --------------------------------------------------------------------------


def test_semantic_review_does_not_depend_on_persistence() -> None:
    """Sprint 09 keeps working with no database at all.

    Persistence depends on the semantic domain, never the reverse. An arrow the
    other way would make the advisory model a storage concern, and the next step
    is storage deciding what a suggestion means.
    """
    offenders = [
        f"{path}: {module}"
        for path in sorted(SEMANTIC_REVIEW_PACKAGE.rglob("*.py"))
        for module in imported_modules(path)
        if module.split(".")[0] in ("review_persistence", "review_application")
    ]
    assert offenders == []


def test_the_application_layer_still_does_not_depend_on_persistence() -> None:
    """Phase D hardening, re-asserted now that Phase E added a storage path."""
    offenders = [
        f"{path}: {module}"
        for path in sorted(Path("review_application").rglob("*.py"))
        for module in imported_modules(path)
        if module.split(".")[0] in ("review_persistence", "sqlite3")
    ]
    assert offenders == []


def test_persistence_uses_the_sprint_09_model_rather_than_its_own() -> None:
    """One semantic suggestion model in the codebase, and it belongs to Sprint 09."""
    assert "semantic_review.models" in imported_modules(SEMANTIC_MAPPER_SOURCE)

    defined = {
        node.name
        for node in ast.walk(ast.parse(SEMANTIC_MAPPER_SOURCE.read_text("utf-8")))
        if isinstance(node, ast.ClassDef)
    }
    assert defined == set(), f"Persistence defined its own semantic types: {sorted(defined)}"
    assert semantic_mapper.SemanticSuggestion.__module__ == "semantic_review.models"


# --------------------------------------------------------------------------
# No LLM, no network
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        Path("review_persistence/sqlite/semantic_mapper.py"),
        Path("review_persistence/sqlite/review_repository.py"),
        Path("review_application/service.py"),
    ],
)
def test_no_persistence_module_can_call_a_provider(path: Path) -> None:
    code = code_without_prose(path)
    for token in (
        "OpenAIProvider",
        "SemanticReviewService",
        "suggest_for_case",
        ".suggest(",
        "OPENAI_API_KEY",
        "openai",
        "httpx",
        "requests",
        "urllib",
        "socket",
        "http://",
        "https://",
    ):
        assert token not in code, f"{path}: {token}"
