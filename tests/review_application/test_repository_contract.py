from __future__ import annotations

import ast
import dataclasses
import inspect
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, Protocol, get_type_hints

import pytest

from entity_resolution.models import EntityRecord
from human_review.models import ReviewAuditEntry, ReviewCase, ReviewStatus, ReviewWorkflowState
from review_application.models import PersistedCase, ReviewEvent, WorkflowBundle
from review_application.repository import ReviewCaseRepository
from semantic_review.models import SemanticSuggestion

# The whole persistence surface, frozen. A new method has to be added here
# deliberately, which is the point: the review that adds a method is the review
# that decides whether it can manufacture a decision.
EXPECTED_REPOSITORY_METHODS = frozenset(
    {
        # Sprint 14 Phase A. Bounds one serialized write scope so that the
        # queue state Sprint 08 authorized against is the state the resolution
        # commits onto. It writes nothing itself and names nothing writable.
        "unit_of_work",
        "register_workflow",
        "get_case",
        "list_cases",
        "count_cases",
        "load_workflow_bundle",
        "apply_resolution",
        "list_events",
        "record_semantic_suggestion",
        "list_semantic_suggestions",
    }
)

# Methods that write. None may accept a decision or a status, because a
# transition is only ever produced by ReviewWorkflow.resolve_case.
MUTATING_METHODS = frozenset(
    {"register_workflow", "apply_resolution", "record_semantic_suggestion"}
)

# Named in the Sprint 10 architecture as APIs that must never exist here.
FORBIDDEN_METHOD_NAMES = frozenset(
    {
        "append_event",
        "authorize_match",
        "bypass_authorization",
        "decide",
        "force_match",
        "force_no_match",
        "mark_match",
        "override_status",
        "resolve_case",
        "resolve_without_authorization",
        "set_status",
        "update_status",
    }
)

# Substrings that betray a bypass under a name nobody predicted.
FORBIDDEN_METHOD_FRAGMENTS = (
    "force",
    "bypass",
    "override",
    "authoriz",
    "set_status",
    "update_status",
)

# Parameters that would let a caller name a decision instead of proving one.
FORBIDDEN_MUTATION_PARAMETERS = frozenset({"status", "decision", "human_decision", "new_status"})

SPRINT_08_AUDIT_ENTRY_FIELDS = (
    "review_case_id",
    "record_a_id",
    "record_b_id",
    "machine_decision",
    "machine_reason",
    "human_decision",
    "reviewer_id",
    "resolution_sequence",
    "downstream_action",
)

SPRINT_10_PACKAGES = ("review_application", "review_persistence")
DOMAIN_PACKAGES = ("human_review", "entity_resolution", "semantic_review", "survivorship")


def _public_methods() -> dict[str, Any]:
    return {
        name: member
        for name, member in vars(ReviewCaseRepository).items()
        if not name.startswith("_") and callable(member)
    }


def test_review_case_repository_is_a_protocol() -> None:
    assert ReviewCaseRepository.__bases__ == (Protocol,)
    assert getattr(ReviewCaseRepository, "_is_protocol", False) is True


def test_protocol_cannot_be_instantiated() -> None:
    # A Protocol is a contract, not a base class carrying behaviour.
    with pytest.raises(TypeError):
        ReviewCaseRepository()


def test_repository_surface_is_exactly_the_agreed_set() -> None:
    assert set(_public_methods()) == EXPECTED_REPOSITORY_METHODS


def test_repository_exposes_no_authorization_bypass_method() -> None:
    names = set(_public_methods())

    assert not names & FORBIDDEN_METHOD_NAMES

    offenders = [
        name for name in names if any(fragment in name for fragment in FORBIDDEN_METHOD_FRAGMENTS)
    ]
    assert not offenders, f"Repository method names suggest a decision bypass: {offenders}"


def test_mutating_methods_never_accept_a_decision_or_status() -> None:
    methods = _public_methods()
    offenders = [
        f"{name}({parameter})"
        for name in sorted(MUTATING_METHODS)
        for parameter in inspect.signature(methods[name]).parameters
        if parameter in FORBIDDEN_MUTATION_PARAMETERS
    ]
    assert not offenders, f"A caller could name a decision through: {offenders}"


def test_read_side_status_filter_is_the_domain_enum_not_a_string() -> None:
    # list_cases may filter by status; accepting a bare str would reintroduce
    # the untyped tokens the CHECK constraints exist to forbid.
    hints = get_type_hints(_public_methods()["list_cases"])
    assert hints["status"] == ReviewStatus | None


def test_apply_resolution_requires_domain_output_and_expected_version() -> None:
    method = _public_methods()["apply_resolution"]
    signature = inspect.signature(method)
    hints = get_type_hints(method)

    assert "expected_version" in signature.parameters
    assert hints["expected_version"] is int

    # The decision arrives as the object ReviewWorkflow.resolve_case returned,
    # plus the event projected from its audit entry -- never a status token.
    assert hints["resolved_case"] is ReviewCase
    assert hints["event"] is ReviewEvent
    assert hints["return"] is PersistedCase


def test_repository_returns_application_and_domain_types() -> None:
    hints = {name: get_type_hints(member) for name, member in _public_methods().items()}

    assert hints["get_case"]["return"] is PersistedCase
    assert hints["list_cases"]["return"] == tuple[PersistedCase, ...]
    assert hints["count_cases"]["return"] is int
    assert hints["load_workflow_bundle"]["return"] is WorkflowBundle
    assert hints["list_events"]["return"] == tuple[ReviewEvent, ...]
    assert hints["list_semantic_suggestions"]["return"] == tuple[SemanticSuggestion, ...]
    assert hints["register_workflow"]["state"] is ReviewWorkflowState
    assert hints["record_semantic_suggestion"]["suggestion"] is SemanticSuggestion


def test_every_repository_method_documents_its_contract() -> None:
    undocumented = [name for name, member in _public_methods().items() if not member.__doc__]
    assert not undocumented


def test_unit_of_work_takes_nothing_and_yields_no_storage_handle() -> None:
    """The scope seam must not become a way into the database.

    It is the one Protocol method that exists for the application layer's
    benefit rather than to move data, so the risk is not that it writes
    something but that it hands back a connection. Parameterless, and typed as
    a context manager over ``object`` rather than over anything a caller could
    execute SQL on.
    """
    method = _public_methods()["unit_of_work"]
    signature = inspect.signature(method)

    assert list(signature.parameters) == ["self"]

    hints = get_type_hints(method)
    assert hints["return"] == AbstractContextManager[object]


class _ConformingRepository:
    """A structural implementation, proving the Protocol is satisfiable as written."""

    @contextmanager
    def unit_of_work(self) -> Iterator[None]:
        raise NotImplementedError
        yield  # pragma: no cover - unreachable, present so this is a generator

    def register_workflow(
        self,
        state: ReviewWorkflowState,
        *,
        entity_records: Sequence[EntityRecord],
        resolution_snapshot: Mapping[str, Any],
        entity_resolution_config_path: str | None,
        now_utc: str,
    ) -> tuple[PersistedCase, ...]:
        raise NotImplementedError

    def get_case(self, review_case_id: str) -> PersistedCase:
        raise NotImplementedError

    def list_cases(
        self,
        *,
        status: ReviewStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[PersistedCase, ...]:
        raise NotImplementedError

    def count_cases(self, *, status: ReviewStatus | None = None) -> int:
        raise NotImplementedError

    def load_workflow_bundle(self) -> WorkflowBundle:
        raise NotImplementedError

    def apply_resolution(
        self,
        resolved_case: ReviewCase,
        *,
        expected_version: int,
        event: ReviewEvent,
        now_utc: str,
    ) -> PersistedCase:
        raise NotImplementedError

    def list_events(self, review_case_id: str) -> tuple[ReviewEvent, ...]:
        raise NotImplementedError

    def record_semantic_suggestion(self, suggestion: SemanticSuggestion, *, now_utc: str) -> bool:
        raise NotImplementedError

    def list_semantic_suggestions(self, review_case_id: str) -> tuple[SemanticSuggestion, ...]:
        raise NotImplementedError


def test_conforming_implementation_matches_every_signature() -> None:
    # Type-checker conformance is asserted by the annotation; the runtime check
    # below catches a Protocol whose parameters no implementation could satisfy.
    repository: ReviewCaseRepository = _ConformingRepository()

    for name, member in _public_methods().items():
        expected = inspect.signature(member)
        actual = inspect.signature(getattr(repository, name))
        assert list(expected.parameters)[1:] == list(actual.parameters), name


def test_sprint_08_audit_entry_contract_unchanged() -> None:
    # Persistence projects this dataclass verbatim into audit_entry_json.
    field_names = tuple(field.name for field in dataclasses.fields(ReviewAuditEntry))
    assert field_names == SPRINT_08_AUDIT_ENTRY_FIELDS


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
    return modules


def test_domain_packages_do_not_depend_on_sprint_10() -> None:
    # The dependency runs application -> domain and never back. A domain module
    # importing persistence would be the first step toward a decision taken in
    # the repository.
    offenders = [
        f"{path}:{module}"
        for package in DOMAIN_PACKAGES
        for path in Path(package).rglob("*.py")
        for module in _imported_modules(path)
        if module.split(".")[0] in SPRINT_10_PACKAGES
    ]
    assert not offenders


def test_sprint_10_adds_no_orm_or_migration_dependency() -> None:
    # Locked architecture: stdlib sqlite3 only.
    banned = re.compile(r"sqlalchemy|alembic", re.IGNORECASE)
    for package in SPRINT_10_PACKAGES:
        for path in Path(package).rglob("*.py"):
            assert not banned.search(path.read_text(encoding="utf-8")), path
    assert not banned.search(Path("pyproject.toml").read_text(encoding="utf-8"))
