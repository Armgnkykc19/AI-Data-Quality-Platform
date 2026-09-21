"""``python -m review_api``: the one official way to serve this API.

Nothing here binds a socket. ``uvicorn.run`` is replaced with a recorder, which
is the point rather than a convenience -- what has to be pinned is the
arguments the runner hands to the server, and those are exactly the three
runtime decisions Sprint 11 is not free to get wrong: a loopback host, one
worker, and no reloader.

The loopback rule is the security-relevant one. ``POST .../resolve`` is an
authoritative write over customer-derived evidence, and this API has no
authentication and no verified reviewer identity. A default of ``0.0.0.0``, or
a host argument that was merely passed through, would publish that endpoint to
the network.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI

from review_api import __main__ as runtime
from tests.review_api.test_api_layering import run_guarded

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SOURCE = Path("review_api/__main__.py")


class RecordedRun:
    """Stands in for ``uvicorn.run`` and remembers what it was asked to serve."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def __call__(self, app: Any, **kwargs: Any) -> None:
        self.calls.append((app, kwargs))

    @property
    def called(self) -> bool:
        return bool(self.calls)

    @property
    def app(self) -> Any:
        return self.calls[0][0]

    @property
    def kwargs(self) -> dict[str, Any]:
        return self.calls[0][1]


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> RecordedRun:
    """A runner whose queue exists and whose server is a recorder.

    ``configured_database_path`` is patched to ``tmp_path`` so no test can read
    -- or create -- the real ``storage/review_queue.db``.
    """
    database = tmp_path / "review_queue.db"
    database.write_bytes(b"")
    monkeypatch.setattr(runtime, "configured_database_path", lambda: database)
    recorder = RecordedRun()
    monkeypatch.setattr(uvicorn, "run", recorder)
    return recorder


@pytest.fixture
def missing_queue(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> RecordedRun:
    """A runner pointed at a queue that was never registered."""
    monkeypatch.setattr(
        runtime,
        "configured_database_path",
        lambda: tmp_path / "never_registered.db",
    )
    recorder = RecordedRun()
    monkeypatch.setattr(uvicorn, "run", recorder)
    return recorder


# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------


def test_the_defaults_are_localhost_and_port_8000(served: RecordedRun) -> None:
    assert runtime.main([]) == 0

    assert served.kwargs["host"] == "127.0.0.1"
    assert served.kwargs["port"] == 8000


def test_the_declared_defaults_match_what_is_served(served: RecordedRun) -> None:
    """The constants are documentation; this is what makes them true."""
    runtime.main([])

    assert served.kwargs["host"] == runtime.DEFAULT_HOST
    assert served.kwargs["port"] == runtime.DEFAULT_PORT


def test_one_worker_and_no_reloader(served: RecordedRun) -> None:
    """Sprint 10 holds one sqlite3 connection, usable on one thread.

    A second worker is a second process with its own connection to the same
    file, and the reloader restarts the application underneath a request.
    """
    runtime.main([])

    assert served.kwargs["workers"] == 1
    assert served.kwargs["reload"] is False
    assert runtime.WORKERS == 1


def test_the_runtime_serves_the_production_application(served: RecordedRun) -> None:
    """Not ``create_app()``: the lifespan that owns the queue must be attached."""
    runtime.main([])

    app = served.app
    assert isinstance(app, FastAPI)
    assert app.router.lifespan_context is not None
    assert app.debug is False


def test_the_served_application_publishes_the_tenant_scoped_surface(
    served: RecordedRun,
) -> None:
    """Read from the published schema, not from Starlette's internal route list.

    ``app.routes`` holds an included router as an opaque object with no ``path``
    from Starlette 1.0 onward. ``app.openapi()`` is the framework's public
    answer to "what does this application publish", and it builds the document
    without a client and without entering the lifespan -- so this test still
    never opens a database.

    The unscoped assertion is the important half: what the runner serves must
    have no review path that omits a tenant, because that is the shape an
    operator would actually be exposing.
    """
    runtime.main([])

    paths = served.app.openapi()["paths"]
    assert "/health" in paths
    assert (
        "/api/v1/organizations/{organization_id}/review-queues/{review_queue_id}"
        "/review-cases/{review_case_id}/resolve" in paths
    )
    assert not [path for path in paths if path.startswith("/api/v1/review-cases")]


# --------------------------------------------------------------------------
# The loopback rule
# --------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.2"])
def test_loopback_addresses_are_accepted(served: RecordedRun, host: str) -> None:
    assert runtime.main(["--host", host]) == 0

    assert served.kwargs["host"] == host


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "::",
        "10.0.0.5",
        "192.168.1.20",
        "203.0.113.7",
        "example.com",
        "",
    ],
)
def test_a_non_loopback_host_is_refused_and_nothing_is_served(
    served: RecordedRun,
    host: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = runtime.main(["--host", host])

    assert exit_code == runtime.EXIT_USAGE
    assert not served.called
    assert "localhost only" in capsys.readouterr().out


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.42"])
def test_is_loopback_accepts_only_unreachable_addresses(host: str) -> None:
    assert runtime.is_loopback(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "8.8.8.8", "example.com", "localhost."])
def test_is_loopback_rejects_everything_reachable(host: str) -> None:
    assert not runtime.is_loopback(host)


def test_a_hostname_is_refused_rather_than_resolved() -> None:
    """Refusing beats a DNS lookup deciding whether this API is reachable.

    A name that resolves to a loopback address today can resolve elsewhere
    tomorrow, and the answer would come from outside this process.
    """
    assert not runtime.is_loopback("my-laptop.local")


# --------------------------------------------------------------------------
# Port
# --------------------------------------------------------------------------


@pytest.mark.parametrize("port", [1, 8000, 65535])
def test_a_port_in_range_is_served(served: RecordedRun, port: int) -> None:
    assert runtime.main(["--port", str(port)]) == 0

    assert served.kwargs["port"] == port


@pytest.mark.parametrize("port", [0, -1, 65536, 99999])
def test_a_port_out_of_range_is_refused(served: RecordedRun, port: int) -> None:
    assert runtime.main(["--port", str(port)]) == runtime.EXIT_USAGE

    assert not served.called


def test_a_non_numeric_port_is_rejected_by_the_parser(served: RecordedRun) -> None:
    with pytest.raises(SystemExit):
        runtime.main(["--port", "eight-thousand"])

    assert not served.called


# --------------------------------------------------------------------------
# Bootstrap before use
# --------------------------------------------------------------------------


def test_an_unregistered_queue_stops_the_server_starting(
    missing_queue: RecordedRun,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Starting anyway would create the file and serve a convincing empty queue.

    An empty schema looks exactly like a queue whose cases have all been
    reviewed, so the runner refuses instead of manufacturing one.
    """
    exit_code = runtime.main([])

    assert exit_code == runtime.EXIT_NOT_BOOTSTRAPPED
    assert not missing_queue.called


def test_the_refusal_names_the_command_that_fixes_it(
    missing_queue: RecordedRun,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime.main([])

    out = capsys.readouterr().out
    assert "--register-review-queue" in out
    assert "manage_human_review.py" in out


def test_the_refusal_creates_no_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "never_registered.db"
    monkeypatch.setattr(runtime, "configured_database_path", lambda: database)
    monkeypatch.setattr(uvicorn, "run", RecordedRun())

    runtime.main([])

    assert not database.exists()
    assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------
# The database path is not a client-visible knob
# --------------------------------------------------------------------------


def test_the_runner_exposes_no_database_option(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The queue location is operator configuration the lifespan reads itself.

    A flag here could only disagree with what the lifespan actually opens.
    """
    with pytest.raises(SystemExit):
        runtime.parse_args(["--help"])

    out = capsys.readouterr().out
    assert "--review-db" not in out
    assert "--database" not in out


def test_the_runner_passes_no_path_to_the_server(served: RecordedRun) -> None:
    runtime.main([])

    assert "database" not in served.kwargs
    assert set(served.kwargs) == {"host", "port", "workers", "reload"}


def test_the_help_states_the_localhost_boundary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        runtime.parse_args(["--help"])

    out = capsys.readouterr().out
    assert "Localhost only" in out
    assert "no authentication" in out


# --------------------------------------------------------------------------
# Import safety
# --------------------------------------------------------------------------


def test_importing_the_runtime_module_opens_no_database_and_no_socket() -> None:
    """Importing the entry point must not start anything, or read the queue."""
    result = run_guarded("import review_api.__main__\nprint('OK')\n")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"


def test_importing_the_runtime_module_creates_no_storage_directory(
    tmp_path: Path,
) -> None:
    """Run from a scratch directory, so a created ``storage/`` would be visible."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.path.insert(0, {str(PROJECT_ROOT)!r}); "
            "import review_api.__main__; print('OK')",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_the_runtime_module_reaches_storage_only_through_the_composition_root() -> None:
    """It asks ``review_api.dependencies`` where the queue is; it never looks."""
    tree = ast.parse(RUNTIME_SOURCE.read_text(encoding="utf-8"))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) if node.module
    }

    assert "review_api.dependencies" in imported
    assert not [module for module in imported if module.split(".")[0] == "review_persistence"]


def test_the_runtime_module_builds_the_app_through_the_production_factory() -> None:
    source = RUNTIME_SOURCE.read_text(encoding="utf-8")

    assert "create_production_app" in source
    # A module-level application would be constructed at import time, which is
    # what every import-safety test above exists to prevent.
    assert "app = FastAPI(" not in source
