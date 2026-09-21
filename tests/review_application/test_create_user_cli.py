"""The operator command that creates a login, and what it refuses to do.

Two properties carry the weight here.

**The password never travels as an argument.** There is no ``--password``, and
there must never be: a command-line argument is written into shell history and
is visible in the process list to every other user on the machine. Both are
places a credential outlives the moment it was needed.

**Provisioning is atomic.** A failure must not leave a user row with no
credential -- an account that exists, looks fine, and cannot be signed into for
a reason nothing reports. Every failure path below asserts on durable state
rather than only on the exit code.

``getpass`` is replaced in these tests rather than driven, because an automated
suite must never block on a terminal prompt.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from review_persistence.config import ReviewPersistenceConfig
from review_persistence.sqlite.credential_repository import SqliteCredentialRepository
from review_persistence.sqlite.database import open_review_database
from review_persistence.sqlite.tenant_repository import SqliteTenantRepository
from scripts import manage_human_review

EMAIL = "reviewer@example.test"
DISPLAY_NAME = "Reviewer One"
PASSWORD = "correct horse battery staple"


@pytest.fixture
def review_db(tmp_path: Path) -> Path:
    """Never the configured production queue."""
    return tmp_path / "identity.db"


def fake_getpass(monkeypatch: pytest.MonkeyPatch, *responses: str) -> list[str]:
    """Answer each prompt in turn, recording the prompts that were shown.

    Substituted on the module under test rather than on the ``getpass`` module
    globally, so nothing outside this command is affected.
    """
    prompts: list[str] = []
    answers = list(responses)

    def _getpass(prompt: str = "") -> str:
        prompts.append(prompt)
        return answers.pop(0)

    monkeypatch.setattr(manage_human_review.getpass, "getpass", _getpass)
    return prompts


def run(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["manage_human_review.py", *argv])
    return manage_human_review.main()


def create_user(
    monkeypatch: pytest.MonkeyPatch,
    review_db: Path,
    *,
    email: str = EMAIL,
    display_name: str = DISPLAY_NAME,
) -> int:
    return run(
        monkeypatch,
        "create-user",
        "--email",
        email,
        "--display-name",
        display_name,
        "--review-db",
        str(review_db),
    )


def open_control(review_db: Path):
    config = ReviewPersistenceConfig(
        database_path=review_db, busy_timeout_ms=2000, journal_mode="WAL"
    )
    return open_review_database(config)


def stored_counts(review_db: Path) -> dict[str, int]:
    if not review_db.exists():
        return {}
    database = open_control(review_db)
    try:
        connection = database.connect()
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "users",
                "password_credentials",
                "organizations",
                "organization_memberships",
                "review_queues",
            )
        }
    finally:
        database.close()


# --------------------------------------------------------------------------
# The password is never an argument
# --------------------------------------------------------------------------


def test_password_is_not_an_accepted_option(
    monkeypatch: pytest.MonkeyPatch,
    review_db: Path,
) -> None:
    """A credential in argv is a credential in shell history and in ``ps``."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "manage_human_review.py",
            "create-user",
            "--email",
            EMAIL,
            "--display-name",
            DISPLAY_NAME,
            "--password",
            PASSWORD,
        ],
    )

    with pytest.raises(SystemExit) as exit_info:
        manage_human_review.parse_args()

    assert exit_info.value.code != 0


def test_the_help_text_offers_no_password_option(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["manage_human_review.py", "create-user", "--help"])

    with pytest.raises(SystemExit):
        manage_human_review.parse_args()

    assert "--password" not in capsys.readouterr().out


def test_the_password_is_read_without_echo_and_confirmed(
    monkeypatch: pytest.MonkeyPatch,
    review_db: Path,
) -> None:
    """``getpass`` keeps the characters off the screen and out of scrollback."""
    prompts = fake_getpass(monkeypatch, PASSWORD, PASSWORD)

    assert create_user(monkeypatch, review_db) == 0
    assert len(prompts) == 2
    assert "confirm" in prompts[1].lower()


# --------------------------------------------------------------------------
# Success
# --------------------------------------------------------------------------


def test_a_successful_create_stores_one_user_and_one_credential(
    monkeypatch: pytest.MonkeyPatch,
    review_db: Path,
) -> None:
    fake_getpass(monkeypatch, PASSWORD, PASSWORD)

    assert create_user(monkeypatch, review_db) == 0

    counts = stored_counts(review_db)
    assert counts["users"] == 1
    assert counts["password_credentials"] == 1


def test_the_stored_credential_is_argon2id_and_not_the_password(
    monkeypatch: pytest.MonkeyPatch,
    review_db: Path,
) -> None:
    fake_getpass(monkeypatch, PASSWORD, PASSWORD)
    create_user(monkeypatch, review_db)

    database = open_control(review_db)
    try:
        user = SqliteTenantRepository(database).get_user_by_email(EMAIL)
        assert user is not None
        credential = SqliteCredentialRepository(database).get_credential(user.user_id)
        assert credential is not None
        assert credential.password_hash.startswith("$argon2id$")
        assert PASSWORD not in credential.password_hash
    finally:
        database.close()


def test_the_plaintext_appears_nowhere_in_the_database(
    monkeypatch: pytest.MonkeyPatch,
    review_db: Path,
) -> None:
    fake_getpass(monkeypatch, PASSWORD, PASSWORD)
    create_user(monkeypatch, review_db)

    database = open_control(review_db)
    try:
        connection = database.connect()
        tables = [
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            for row in connection.execute(f"SELECT * FROM {table}"):  # noqa: S608 - schema names
                for value in tuple(row):
                    assert not (isinstance(value, str) and PASSWORD in value)
    finally:
        database.close()


def test_creating_a_user_creates_no_organization_membership_or_queue(
    monkeypatch: pytest.MonkeyPatch,
    review_db: Path,
) -> None:
    """Identity provisioning and authorization provisioning stay separate.

    A command that did both would mean creating an account implied granting it
    access to something.
    """
    fake_getpass(monkeypatch, PASSWORD, PASSWORD)
    create_user(monkeypatch, review_db)

    counts = stored_counts(review_db)
    assert counts["organizations"] == 0
    assert counts["organization_memberships"] == 0
    assert counts["review_queues"] == 0


def test_the_output_carries_no_password_and_no_hash(
    monkeypatch: pytest.MonkeyPatch,
    review_db: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_getpass(monkeypatch, PASSWORD, PASSWORD)
    create_user(monkeypatch, review_db)

    out = capsys.readouterr().out

    assert PASSWORD not in out
    assert "argon2" not in out
    assert "hash" not in out.lower()
    # What it does say: the address the operator typed and the opaque id.
    assert EMAIL in out
    assert "USR-" in out


def test_the_output_says_the_user_has_no_membership_yet(
    monkeypatch: pytest.MonkeyPatch,
    review_db: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """So an operator is not surprised that the new account can see nothing."""
    fake_getpass(monkeypatch, PASSWORD, PASSWORD)
    create_user(monkeypatch, review_db)

    assert "no organization membership" in capsys.readouterr().out.lower()


def test_the_login_handle_is_normalized_the_phase_b_way(
    monkeypatch: pytest.MonkeyPatch,
    review_db: Path,
) -> None:
    fake_getpass(monkeypatch, PASSWORD, PASSWORD)
    create_user(monkeypatch, review_db, email="Reviewer@Example.Test")

    database = open_control(review_db)
    try:
        assert SqliteTenantRepository(database).get_user_by_email(EMAIL) is not None
    finally:
        database.close()


# --------------------------------------------------------------------------
# Failure leaves nothing behind
# --------------------------------------------------------------------------


def test_a_password_mismatch_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    review_db: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """There is no reset flow, so a typo would produce an unusable account."""
    fake_getpass(monkeypatch, PASSWORD, PASSWORD + "x")

    exit_code = create_user(monkeypatch, review_db)

    assert exit_code == 1
    assert "did not match" in capsys.readouterr().out
    # The database was never even opened.
    assert stored_counts(review_db) == {}


@pytest.mark.parametrize(
    ("password", "label"),
    [("short", "below the minimum"), ("a" * 300, "above the maximum")],
)
def test_a_password_the_policy_rejects_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    review_db: Path,
    capsys: pytest.CaptureFixture[str],
    password: str,
    label: str,
) -> None:
    """Checked before hashing, so a rejected password never reaches storage."""
    fake_getpass(monkeypatch, password, password)

    exit_code = create_user(monkeypatch, review_db)

    assert exit_code == 1, label
    counts = stored_counts(review_db)
    assert counts.get("users", 0) == 0
    assert counts.get("password_credentials", 0) == 0


def test_a_rejected_password_is_never_echoed(
    monkeypatch: pytest.MonkeyPatch,
    review_db: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "sh0rt"
    fake_getpass(monkeypatch, secret, secret)

    create_user(monkeypatch, review_db)

    assert secret not in capsys.readouterr().out


def test_a_malformed_address_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    review_db: Path,
) -> None:
    fake_getpass(monkeypatch, PASSWORD, PASSWORD)

    exit_code = create_user(monkeypatch, review_db, email="not-an-address")

    assert exit_code == 1
    counts = stored_counts(review_db)
    assert counts.get("users", 0) == 0
    assert counts.get("password_credentials", 0) == 0


def test_a_duplicate_address_is_refused_deterministically(
    monkeypatch: pytest.MonkeyPatch,
    review_db: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_getpass(monkeypatch, PASSWORD, PASSWORD)
    assert create_user(monkeypatch, review_db) == 0

    fake_getpass(monkeypatch, PASSWORD, PASSWORD)
    exit_code = create_user(monkeypatch, review_db, email="REVIEWER@example.test")

    assert exit_code == 6
    counts = stored_counts(review_db)
    assert counts["users"] == 1
    assert counts["password_credentials"] == 1


def test_a_duplicate_refusal_does_not_echo_the_address(
    monkeypatch: pytest.MonkeyPatch,
    review_db: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The message must not become an account-existence oracle if this path
    ever becomes reachable by anyone but an operator."""
    fake_getpass(monkeypatch, PASSWORD, PASSWORD)
    create_user(monkeypatch, review_db)
    capsys.readouterr()

    fake_getpass(monkeypatch, PASSWORD, PASSWORD)
    create_user(monkeypatch, review_db)

    assert EMAIL not in capsys.readouterr().out


def test_a_failure_writing_the_credential_rolls_the_user_back(
    monkeypatch: pytest.MonkeyPatch,
    review_db: Path,
) -> None:
    """The atomicity guarantee, forced.

    The credential insert is made to fail after the user insert has already
    run inside the same transaction. Both must disappear -- otherwise a
    database failure could leave an account nobody can sign into and nothing
    reports.
    """
    fake_getpass(monkeypatch, PASSWORD, PASSWORD)

    from review_persistence.sqlite.credential_repository import (
        SqliteCredentialRepository as Repo,
    )

    def explode(connection, credential):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(Repo, "upsert_credential_in", staticmethod(explode))

    with pytest.raises(sqlite3.OperationalError):
        create_user(monkeypatch, review_db)

    monkeypatch.undo()
    counts = stored_counts(review_db)
    assert counts["users"] == 0
    assert counts["password_credentials"] == 0


def test_the_new_user_can_actually_log_in(
    monkeypatch: pytest.MonkeyPatch,
    review_db: Path,
) -> None:
    """The point of the command: an account that authenticates.

    Driven through the real Phase C login orchestration, so this fails if the
    stored verifier does not match what an operator typed.
    """
    from identity.authentication import AuthenticationService
    from identity.login import LoginService
    from identity.passwords import Argon2idPasswordHasher
    from identity.session_service import SessionService
    from review_persistence.sqlite.session_repository import SqliteSessionRepository

    fake_getpass(monkeypatch, PASSWORD, PASSWORD)
    assert create_user(monkeypatch, review_db) == 0

    database = open_control(review_db)
    try:
        users = SqliteTenantRepository(database)
        logins = LoginService(
            authentication=AuthenticationService(
                users=users,
                credentials=SqliteCredentialRepository(database),
                hasher=Argon2idPasswordHasher(),
            ),
            sessions=SessionService(sessions=SqliteSessionRepository(database), users=users),
        )

        result = logins.login(EMAIL, PASSWORD)

        assert result.user.display_name == DISPLAY_NAME
        assert result.created.raw_token
    finally:
        database.close()
