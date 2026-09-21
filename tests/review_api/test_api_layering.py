"""Dependency direction and import safety, asserted rather than assumed.

Sprint 11 adds a transport layer above an architecture that already decided
where authority lives. The arrows must keep pointing one way: HTTP depends on
``review_application``, which depends on a Protocol, which ``review_persistence``
implements. A route that reached past the Protocol into SQLite would keep
working perfectly, right up until the storage layer changed underneath it.

The import-safety tests cover a different failure with the same quality: nothing
goes wrong visibly when importing a package opens a database. It just means a
test run, a linter, or a ``--help`` invocation touches the real review queue.

Written in the style of ``tests/review_application/test_layering.py``, which
already pins the Sprint 10 half of the same rule.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from review_api import create_app
from review_api.dependencies import get_queue_binder, get_tenant_authorization

PROJECT_ROOT = Path(__file__).resolve().parents[2]
API_PACKAGE = Path("review_api")
ROUTES_PACKAGE = API_PACKAGE / "routes"

# The single production module allowed to name the concrete storage backend.
COMPOSITION_ROOT = API_PACKAGE / "dependencies.py"

DOMAIN_PACKAGES = (
    "review_application",
    "review_persistence",
    "human_review",
    "semantic_review",
    "entity_resolution",
    "survivorship",
)

# Modules no part of the HTTP layer may import. Authorization internals and
# union-find live in the first group: reimplementing or even reaching for them
# from a route is how a second, drifting copy of MATCH authorization gets born.
# The rest are provider internals -- an HTTP request path must not be able to
# reach a model provider.
FORBIDDEN_ANYWHERE_IN_API = (
    "sqlite3",
    "human_review.authorization",
    "human_review.constraints",
    "human_review.safety",
    # The workflow engine itself. The resolution endpoint goes through
    # ``ReviewQueueService``, which builds a fresh ``ReviewWorkflow`` per call
    # against the complete persisted bundle. An HTTP layer holding one would be
    # a second application layer, deciding for itself which state to authorize
    # against.
    "human_review.workflow",
    "human_review.cases",
    # The AUTO_MATCH reconstruction seam. Reconstructing the graph here would
    # mean the API had the authorization input in its hands.
    "human_review.reporting",
    "semantic_review.service",
    "semantic_review.provider",
    "semantic_review.providers",
    "entity_resolution.engine",
    "entity_resolution.clustering",
    "entity_resolution.config",
    "openai",
)

# Storage vocabulary that should never be spelled in the HTTP layer.
FORBIDDEN_TOKENS_IN_API = (
    "sqlite3",
    "PRAGMA",
    "BEGIN IMMEDIATE",
    "review_case_events",
    "schema_meta",
    "DATABASE_SCHEMA_VERSION",
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


def code_without_prose(path: Path) -> str:
    """Executable code only.

    Docstrings in this layer legitimately explain what the storage layer does
    and why the API stays away from it; what matters is whether the code names
    any of it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if ast.get_docstring(node) is not None:
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def api_modules() -> list[Path]:
    return sorted(API_PACKAGE.rglob("*.py"))


def route_modules() -> list[Path]:
    return sorted(ROUTES_PACKAGE.rglob("*.py"))


def test_the_api_package_was_found() -> None:
    """Guards every negative assertion below from passing on an empty list."""
    assert len(api_modules()) >= 5
    assert route_modules()


# --------------------------------------------------------------------------
# Routes stay above the application layer
# --------------------------------------------------------------------------


def test_no_route_module_imports_persistence() -> None:
    offenders = [
        f"{path}: {module}"
        for path in route_modules()
        for module in imported_modules(path)
        if module.split(".")[0] in ("review_persistence", "sqlite3")
    ]
    assert offenders == []


def test_only_the_composition_root_imports_persistence() -> None:
    """One module knows the queue is SQLite. Everything else knows a Protocol."""
    importers = {
        str(path)
        for path in api_modules()
        for module in imported_modules(path)
        if module.split(".")[0] == "review_persistence"
    }
    assert importers == {str(COMPOSITION_ROOT)}


def test_the_composition_root_imports_only_the_public_persistence_surface() -> None:
    """Mapper and connection internals are off limits even to the wiring module."""
    internals = {
        "review_persistence.sqlite.mapper",
        "review_persistence.sqlite.event_mapper",
        "review_persistence.sqlite.semantic_mapper",
        "review_persistence.sqlite.context_mapper",
        "review_persistence.sqlite.database",
        "review_persistence.sqlite.review_repository",
    }
    assert imported_modules(COMPOSITION_ROOT) & internals == set()


@pytest.mark.parametrize("module", FORBIDDEN_ANYWHERE_IN_API)
def test_the_api_imports_no_domain_or_provider_internals(module: str) -> None:
    offenders = [
        str(path)
        for path in api_modules()
        for imported in imported_modules(path)
        if imported == module or imported.startswith(f"{module}.")
    ]
    assert offenders == []


@pytest.mark.parametrize("token", FORBIDDEN_TOKENS_IN_API)
def test_the_api_names_no_storage_detail(token: str) -> None:
    offenders = [str(path) for path in api_modules() if token in code_without_prose(path)]
    assert offenders == []


def test_the_api_depends_on_the_application_layer() -> None:
    """The allowed direction, pinned so a future refactor cannot quietly invert it."""
    upward = {
        module
        for path in api_modules()
        for module in imported_modules(path)
        if module.split(".")[0] == "review_application"
    }
    assert upward


def test_the_resolution_route_goes_through_the_application_service() -> None:
    """The route module names the service, and reaches the domain no other way."""
    source = code_without_prose(ROUTES_PACKAGE / "review_cases.py")

    # The route reaches the service only through the scoped dependency, which is
    # itself unreachable without a proven tenant scope.
    assert "ScopedServiceDep" in source
    # The authority call, and the only one a route may make to decide anything.
    assert "resolve_case" in source
    # Things a route would have to name if it were deciding for itself.
    for token in (
        "ReviewWorkflow",
        "load_workflow_bundle",
        "apply_resolution",
        "register_workflow",
        "record_semantic_suggestion",
        "rebuild_resolution_from_snapshot",
        "assert_human_match",
        "entity_resolution_config",
        "records_by_id",
        "resolution_snapshot",
    ):
        assert token not in source, f"a route module names {token}"


def imported_names(path: Path) -> set[str]:
    """Names bound by imports, so ``ReviewEvent`` and ``ReviewEventRead`` differ.

    A substring search cannot tell the domain class from the API model named
    after it, and the API model is exactly what a route is supposed to use.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom | ast.Import):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
    return names


def test_no_route_module_constructs_a_domain_event() -> None:
    """Only the service may project a domain audit entry into a history event.

    Routes import ``ReviewEventRead`` -- the published projection -- and never
    ``ReviewEvent`` itself. Building one here would mean the HTTP layer deciding
    what history to record.
    """
    for path in route_modules():
        names = imported_names(path)
        assert "ReviewEvent" not in names
        assert "ReviewAuditEntry" not in names
        assert "from_audit_entry" not in code_without_prose(path)


# --------------------------------------------------------------------------
# Nothing below the API depends on the API
# --------------------------------------------------------------------------


@pytest.mark.parametrize("package", DOMAIN_PACKAGES)
def test_no_lower_layer_imports_the_api(package: str) -> None:
    offenders = [
        f"{path}: {module}"
        for path in sorted(Path(package).rglob("*.py"))
        for module in imported_modules(path)
        if module.split(".")[0] == "review_api"
    ]
    assert offenders == []


def test_no_script_imports_the_api() -> None:
    """The CLIs and the HTTP surface stay independent entry points.

    The operator commands -- creating a tenant, a queue, a user, a membership,
    registering a workflow -- must keep working with no HTTP layer in the
    picture, and the API must not be able to call one.
    """
    offenders = [
        str(path)
        for path in sorted(Path("scripts").glob("*.py"))
        if "review_api" in imported_modules(path)
    ]
    assert offenders == []


# --------------------------------------------------------------------------
# Import safety
# --------------------------------------------------------------------------


def run_guarded(snippet: str) -> subprocess.CompletedProcess[str]:
    """Run a snippet with SQLite and outbound sockets booby-trapped first.

    Patching before the import is what makes this non-vacuous: the guards are in
    place before ``review_api`` is imported, so an import-time database
    connection or network call raises rather than quietly succeeding.

    The network guard wraps ``connect`` rather than replacing the ``socket``
    class, because the stdlib subclasses that class (``ssl.SSLSocket``) and a
    replacement breaks unrelated imports. It also permits loopback: asyncio
    builds its self-pipe from a local socket pair on Windows, so blocking
    every address would fail the test client rather than the thing under test.
    A real provider call goes to a routable host and is still caught.
    """
    guards = (
        "import sqlite3, socket\n"
        "def _no_db(*a, **k):\n"
        "    raise AssertionError('opened a SQLite connection')\n"
        "sqlite3.connect = _no_db\n"
        "_LOCAL = {'127.0.0.1', '::1', 'localhost', ''}\n"
        "_real_connect = socket.socket.connect\n"
        "def _guarded_connect(self, address, *a, **k):\n"
        "    host = address[0] if isinstance(address, tuple) else address\n"
        "    if isinstance(host, str) and host not in _LOCAL:\n"
        "        raise AssertionError('opened a network connection to %s' % host)\n"
        "    return _real_connect(self, address, *a, **k)\n"
        "socket.socket.connect = _guarded_connect\n"
        "def _no_remote(address, *a, **k):\n"
        "    raise AssertionError('opened a network connection')\n"
        "socket.create_connection = _no_remote\n"
    )
    return subprocess.run(
        [sys.executable, "-c", guards + snippet],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_importing_the_package_opens_no_database_and_no_socket() -> None:
    result = run_guarded("import review_api\nprint('OK')\n")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"


def test_building_an_app_opens_no_database_and_no_socket() -> None:
    result = run_guarded(
        "from review_api import create_app, create_production_app\n"
        "create_app()\n"
        # Constructing the production app must also stay inert: the lifespan
        # owns the database, and a lifespan that has not been entered has
        # opened nothing.
        "create_production_app()\n"
        "print('OK')\n"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"


def test_serving_health_opens_no_database_and_no_socket() -> None:
    result = run_guarded(
        "from fastapi.testclient import TestClient\n"
        "from review_api import create_app\n"
        "with TestClient(create_app()) as c:\n"
        "    assert c.get('/health').json() == {'status': 'ok'}\n"
        "print('OK')\n"
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"


def test_no_api_module_imports_a_model_provider() -> None:
    """Restates the parametrized rule as one assertion about the whole package."""
    imported = {module for path in api_modules() for module in imported_modules(path)}

    assert not [module for module in imported if module.split(".")[0] == "openai"]


# --------------------------------------------------------------------------
# The injection seam
# --------------------------------------------------------------------------


def test_dependencies_return_what_was_injected() -> None:
    binder, authorization = object(), object()
    app = create_app(queue_binder=binder, tenant_authorization=authorization)  # type: ignore[arg-type]
    request = SimpleNamespace(app=app)

    assert get_queue_binder(request) is binder  # type: ignore[arg-type]
    assert get_tenant_authorization(request) is authorization  # type: ignore[arg-type]


def test_no_unscoped_repository_or_service_accessor_exists() -> None:
    """The bypass this phase removed, asserted rather than remembered.

    ``get_repository`` and ``get_service`` returned an application-wide,
    single-queue object. Either one surviving would be a way for a route to
    reach review storage without a tenant scope having been proven -- which is
    precisely the shape of the old unauthenticated surface.
    """
    import review_api.dependencies as dependencies

    assert not hasattr(dependencies, "get_repository")
    assert not hasattr(dependencies, "get_service")
    assert not hasattr(dependencies, "resolve_sole_review_queue")


@pytest.mark.parametrize("accessor", [get_queue_binder, get_tenant_authorization])
def test_an_unwired_app_fails_loudly_rather_than_silently(accessor: object) -> None:
    """A deployment fault, not a client error.

    Reaching a storage-backed route on an application nobody wired must raise,
    so the catch-all handler turns it into a static 500 instead of the route
    inventing an empty answer -- or, worse, an unwired authorization service
    letting every request through.
    """
    request = SimpleNamespace(app=create_app())

    with pytest.raises(RuntimeError):
        accessor(request)  # type: ignore[operator]
