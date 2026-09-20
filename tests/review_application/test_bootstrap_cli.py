"""The operator command that registers a durable review queue.

``python scripts/manage_human_review.py generate ... --register-review-queue``
is the one trusted path that populates the Sprint 10 queue. These tests drive
the real command over a real CSV, so what is exercised is the whole chain --
ingestion, quality, entity resolution, Sprint 08 case generation, and
registration -- rather than a hand-assembled workflow.

Two properties get most of the attention.

Registration is opt-in. Generating a report is a read-only artifact; writing the
queue is what a reviewer will later act on, and a command that did it silently
would turn every report run into a write against live human decisions.

And the terminal output is counts. A review case is built from customer records,
so an operator command that printed what it registered would put names, emails
and blocking keys into a shell history.

Every test passes ``--review-db`` into ``tmp_path``. None of them may create
``storage/review_queue.db``.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from human_review.models import HumanReviewDecision, ReviewStatus
from review_application import ReviewQueueService
from review_persistence.config import ReviewPersistenceConfig
from review_persistence.sqlite.database import open_review_database
from review_persistence.sqlite.review_repository import SqliteReviewCaseRepository
from review_persistence.sqlite.tenant_repository import SqliteTenantRepository
from scripts import manage_human_review

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "entity_resolution.yaml"

# Registration now names the tenant that will own the queue. The
# organization must exist first: the command refuses to create one, so a
# mistyped slug cannot turn into a new customer holding review data.
ORGANIZATION_SLUG = "cli-fixture-org"

# Two rows sharing an exact email but conflicting on company, city, district and
# address. Exact email is strong identity evidence, and the location conflicts
# pull the score below AUTO_MATCH -- which is precisely the band Sprint 08
# routes to REVIEW. One REVIEW case, produced by the real engine.
REVIEW_CSV = (
    "first_name,last_name,email,phone,company,city,district,address\n"
    "Ali,Yilmaz,ali@example.com,,Acme,Ankara,Cankaya,Street 1\n"
    "Ali,Yilmaz,ali@example.com,,Beta,Izmir,Konak,Other 9\n"
)

# A third row with a different identity. Registering this changes the stored
# entity record set, which is the fail-closed case the queue has to refuse.
EXTENDED_CSV = REVIEW_CSV + "Veli,Demir,veli@example.com,,Gamma,Bursa,Nilufer,Third 3\n"


@pytest.fixture
def csv_path(tmp_path: Path) -> Path:
    path = tmp_path / "input.csv"
    path.write_text(REVIEW_CSV, encoding="utf-8")
    return path


@pytest.fixture
def review_db(tmp_path: Path) -> Path:
    """Never the configured production queue."""
    return tmp_path / "queue.db"


def run(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["manage_human_review.py", *argv])
    return manage_human_review.main()


def create_organization(
    monkeypatch: pytest.MonkeyPatch,
    review_db: Path,
    slug: str = ORGANIZATION_SLUG,
) -> int:
    """Provision the tenant an operator would create before registering."""
    return run(
        monkeypatch,
        "create-organization",
        "--slug",
        slug,
        "--name",
        "CLI Fixture",
        "--review-db",
        str(review_db),
    )


@pytest.fixture
def organization(monkeypatch: pytest.MonkeyPatch, review_db: Path) -> str:
    assert create_organization(monkeypatch, review_db) == 0
    return ORGANIZATION_SLUG


def generate(
    monkeypatch: pytest.MonkeyPatch,
    csv_path: Path,
    report_dir: Path,
    *extra: str,
    entity_resolution_config: Path | None = None,
) -> int:
    """Invoke the generate subcommand the way an operator would type it."""
    leading = (
        ["--entity-resolution-config", str(entity_resolution_config)]
        if entity_resolution_config is not None
        else []
    )
    return run(
        monkeypatch,
        *leading,
        "generate",
        str(csv_path),
        "--report-dir",
        str(report_dir),
        *extra,
    )


def open_queue(review_db: Path) -> tuple[SqliteReviewCaseRepository, object]:
    """Bind to the single queue the command created, the way the API does."""
    config = ReviewPersistenceConfig(
        database_path=review_db,
        busy_timeout_ms=2000,
        journal_mode="WAL",
    )
    database = open_review_database(config)
    queues = SqliteTenantRepository(database).list_all_review_queues()
    assert len(queues) == 1, f"expected one queue, found {len(queues)}"
    return (
        SqliteReviewCaseRepository(database, review_queue_id=queues[0].review_queue_id),
        database,
    )


# --------------------------------------------------------------------------
# Registration is something an operator asks for
# --------------------------------------------------------------------------


def test_generate_alone_writes_no_queue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    csv_path: Path,
    review_db: Path,
) -> None:
    """The default generate run is unchanged: a report, and nothing durable."""
    assert generate(monkeypatch, csv_path, tmp_path / "report") == 0

    assert (tmp_path / "report" / "human_review_report.json").exists()
    assert not review_db.exists()


def test_generate_alone_does_not_open_the_configured_queue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    csv_path: Path,
) -> None:
    """Not even the configured default path is touched without the flag.

    The config loader is patched to a tmp file rather than trusted, so this
    fails loudly if a future change makes registration implicit.
    """
    configured = tmp_path / "configured.db"

    def _config() -> ReviewPersistenceConfig:
        return ReviewPersistenceConfig(
            database_path=configured,
            busy_timeout_ms=2000,
            journal_mode="WAL",
        )

    monkeypatch.setattr(manage_human_review, "load_review_persistence_config", _config)

    assert generate(monkeypatch, csv_path, tmp_path / "report") == 0
    assert not configured.exists()


def test_review_db_without_the_register_flag_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    csv_path: Path,
    review_db: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Silently ignoring it would let an operator believe a queue was written."""
    exit_code = generate(
        monkeypatch,
        csv_path,
        tmp_path / "report",
        "--review-db",
        str(review_db),
    )

    assert exit_code == 1
    assert "--register-review-queue" in capsys.readouterr().out
    assert not review_db.exists()


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_registering_stores_the_generated_case_and_its_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    csv_path: Path,
    review_db: Path,
    organization: str,
) -> None:
    exit_code = register(monkeypatch, csv_path, tmp_path / "report", review_db)

    assert exit_code == 0
    assert review_db.exists()
    repository, database = open_queue(review_db)
    try:
        cases = repository.list_cases()
        assert len(cases) == 1
        assert cases[0].status is ReviewStatus.PENDING
        bundle = repository.load_workflow_bundle()
        assert len(bundle.entity_records) == 2
        assert bundle.entity_resolution_config_path == str(DEFAULT_CONFIG_PATH)
    finally:
        database.close()


def test_the_operator_output_is_counts_and_a_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    csv_path: Path,
    review_db: Path,
    organization: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    capsys.readouterr()
    register(monkeypatch, csv_path, tmp_path / "report", review_db)

    out = capsys.readouterr().out
    assert str(review_db) in out
    assert "1 total (1 pending, 0 resolved)" in out
    assert "Registered 1 new review case(s)." in out


@pytest.mark.parametrize(
    "value",
    [
        "ali@example.com",
        "Yilmaz",
        "Cankaya",
        "Street 1",
        "auto_match_pairs",
        "resolution_snapshot",
    ],
)
def test_no_customer_value_or_authorization_material_reaches_the_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    csv_path: Path,
    review_db: Path,
    organization: str,
    capsys: pytest.CaptureFixture[str],
    value: str,
) -> None:
    assert register(monkeypatch, csv_path, tmp_path / "report", review_db) == 0

    assert value not in capsys.readouterr().out


# --------------------------------------------------------------------------
# Re-running the pipeline, which is the normal operational rhythm
# --------------------------------------------------------------------------


def register(
    monkeypatch: pytest.MonkeyPatch,
    csv_path: Path,
    report_dir: Path,
    review_db: Path,
    *,
    entity_resolution_config: Path | None = None,
    slug: str = ORGANIZATION_SLUG,
) -> int:
    return generate(
        monkeypatch,
        csv_path,
        report_dir,
        "--register-review-queue",
        "--review-db",
        str(review_db),
        "--organization",
        slug,
        entity_resolution_config=entity_resolution_config,
    )


def test_registering_the_same_input_twice_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    csv_path: Path,
    review_db: Path,
    organization: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert register(monkeypatch, csv_path, tmp_path / "first", review_db) == 0
    capsys.readouterr()

    assert register(monkeypatch, csv_path, tmp_path / "second", review_db) == 0

    out = capsys.readouterr().out
    assert "already registered" in out
    repository, database = open_queue(review_db)
    try:
        assert len(repository.list_cases()) == 1
    finally:
        database.close()


def test_a_re_run_after_a_review_leaves_the_decision_standing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    csv_path: Path,
    review_db: Path,
    organization: str,
) -> None:
    """The failure this flag exists to avoid, tested end to end.

    Case generation is deterministic and always emits the PENDING form, so a
    second registration offers storage exactly the case a reviewer has already
    decided. The resolution, the version and the history all have to survive it.
    """
    register(monkeypatch, csv_path, tmp_path / "first", review_db)
    repository, database = open_queue(review_db)
    try:
        case_id = repository.list_cases()[0].review_case_id
        ReviewQueueService(repository).resolve_case(
            case_id,
            decision=HumanReviewDecision.NO_MATCH,
            reviewer_id="reviewer-1",
            expected_version=1,
        )
        before = repository.get_case(case_id)
        events_before = repository.list_events(case_id)
    finally:
        database.close()

    assert register(monkeypatch, csv_path, tmp_path / "second", review_db) == 0

    repository, database = open_queue(review_db)
    try:
        after = repository.get_case(case_id)
        assert after.status is ReviewStatus.NO_MATCH
        assert after.version == before.version
        assert after.case.resolution == before.case.resolution
        assert repository.list_events(case_id) == events_before
    finally:
        database.close()


# --------------------------------------------------------------------------
# Refusals an operator has to be able to read
# --------------------------------------------------------------------------


def test_changed_input_records_are_refused_without_touching_the_queue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    csv_path: Path,
    review_db: Path,
    organization: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An edited input file is the realistic way a stored context stops matching."""
    register(monkeypatch, csv_path, tmp_path / "first", review_db)
    capsys.readouterr()
    extended = tmp_path / "extended.csv"
    extended.write_text(EXTENDED_CSV, encoding="utf-8")

    exit_code = register(monkeypatch, extended, tmp_path / "second", review_db)

    assert exit_code == 5
    repository, database = open_queue(review_db)
    try:
        assert len(repository.list_cases()) == 1
        assert len(repository.load_workflow_bundle().entity_records) == 2
    finally:
        database.close()


def test_a_different_entity_resolution_config_path_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    csv_path: Path,
    review_db: Path,
    organization: str,
) -> None:
    """Same thresholds, different path: the queue still refuses.

    The stored path is how a later resolution finds the configuration the queue
    was generated with, so it is part of the context rather than a label.
    """
    register(monkeypatch, csv_path, tmp_path / "first", review_db)
    copied = tmp_path / "entity_resolution.yaml"
    shutil.copyfile(DEFAULT_CONFIG_PATH, copied)

    exit_code = register(
        monkeypatch,
        csv_path,
        tmp_path / "second",
        review_db,
        entity_resolution_config=copied,
    )

    assert exit_code == 5


def test_a_refusal_prints_one_operator_line_and_no_traceback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    csv_path: Path,
    review_db: Path,
    organization: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    register(monkeypatch, csv_path, tmp_path / "first", review_db)
    capsys.readouterr()
    extended = tmp_path / "extended.csv"
    extended.write_text(EXTENDED_CSV, encoding="utf-8")

    register(monkeypatch, extended, tmp_path / "second", review_db)

    out = capsys.readouterr().out
    assert "Review queue registration refused:" in out
    assert "Traceback" not in out
    for value in ("veli@example.com", "Demir", "Nilufer"):
        assert value not in out


def test_the_new_exit_code_is_documented_in_help(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An operator reading --help must find the code a wrapper script branches on."""
    monkeypatch.setattr(sys, "argv", ["manage_human_review.py", "--help"])

    with pytest.raises(SystemExit):
        manage_human_review.parse_args()

    assert "5 review queue registration refused" in capsys.readouterr().out


# --------------------------------------------------------------------------
# Import safety
# --------------------------------------------------------------------------

# SQLite is booby-trapped before the import under test runs, which is what
# makes these non-vacuous: a module-level connection raises rather than quietly
# succeeding against the configured queue.
GUARD_LINES = (
    "import sqlite3",
    "def _no_db(*a, **k):",
    "    raise AssertionError('opened a SQLite connection at import time')",
    "sqlite3.connect = _no_db",
)

BOOTSTRAP_MODULES = (
    "review_application",
    "review_application.bootstrap",
    "scripts.manage_human_review",
)


def import_guarded(module: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Import one module in a fresh interpreter with SQLite disabled."""
    program = "\n".join(
        (
            f"import sys; sys.path.insert(0, {str(PROJECT_ROOT)!r})",
            *GUARD_LINES,
            f"import {module}",
            "print('OK')",
        )
    )
    return subprocess.run(
        [sys.executable, "-c", program],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.parametrize("module", BOOTSTRAP_MODULES)
def test_importing_the_bootstrap_path_opens_no_database(module: str, tmp_path: Path) -> None:
    """A database is opened by running the command, never by importing it.

    Otherwise a linter, a --help invocation, or a test collection run would
    touch the live review queue.
    """
    result = import_guarded(module, tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"


@pytest.mark.parametrize("module", BOOTSTRAP_MODULES)
def test_importing_the_bootstrap_path_creates_no_storage(module: str, tmp_path: Path) -> None:
    """Run from a scratch directory, so a created ``storage/`` would be visible."""
    result = import_guarded(module, tmp_path)

    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
