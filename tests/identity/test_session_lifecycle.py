"""The session lifecycle, against a deterministic clock and real storage.

Every boundary in this file is asserted at the second, which is only possible
because the clock is injected and time is never actually waited for. The two
bounds mean different things and both are tested at their exact edge:

* **idle** (60 minutes) bounds an *unattended* session -- the walked-away-from
  laptop. Activity pushes it forward.
* **absolute** (8 hours) bounds a session's total life however active it is.
  It is the bound that limits a *stolen* token, which an attacker would
  otherwise keep alive forever simply by using it, so nothing moves it.

The 55-minute warning threshold is not a third bound. It is UX, the server
never enforces it, and a session at 55 minutes is fully valid -- which is
asserted below, because a warning that coincided with expiry would be useless.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from identity.clock import parse_utc_timestamp
from identity.errors import (
    IdentityNotFoundError,
    IdentityValidationError,
    InactiveUserError,
    SessionExpiredError,
    SessionNotFoundError,
    SessionRevokedError,
)
from identity.models import User, UserStatus
from identity.session_service import SessionService
from identity.sessions import (
    DEFAULT_SESSION_POLICY,
    CreatedSession,
    SessionPolicy,
    hash_session_token,
    new_session_token,
)
from review_persistence.sqlite.session_repository import SqliteSessionRepository
from review_persistence.sqlite.tenant_repository import SqliteTenantRepository
from tests.identity.conftest import make_user
from tests.review_persistence.conftest import FrozenClock

MINUTE = 60
HOUR = 60 * MINUTE


# --------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------


def test_the_locked_policy_values() -> None:
    """60 minutes idle, 55-minute warning, 8 hours absolute. Stated once."""
    assert DEFAULT_SESSION_POLICY.idle_timeout == timedelta(minutes=60)
    assert DEFAULT_SESSION_POLICY.warning_after == timedelta(minutes=55)
    assert DEFAULT_SESSION_POLICY.absolute_timeout == timedelta(hours=8)
    assert DEFAULT_SESSION_POLICY.warning_window == timedelta(minutes=5)


def test_the_policy_refuses_an_incoherent_ordering() -> None:
    """A warning at or after expiry warns about something already over, and an
    idle bound at or beyond the absolute bound could never be reached."""
    with pytest.raises(IdentityValidationError, match="warning_after"):
        SessionPolicy(warning_after=timedelta(minutes=60), idle_timeout=timedelta(minutes=60))
    with pytest.raises(IdentityValidationError, match="idle_timeout"):
        SessionPolicy(idle_timeout=timedelta(hours=8), absolute_timeout=timedelta(hours=8))
    with pytest.raises(IdentityValidationError, match="warning_after"):
        SessionPolicy(warning_after=timedelta(0))


def test_the_service_publishes_its_policy(sessions: SessionService) -> None:
    """So a later ``/session`` response reads one value instead of restating 55."""
    assert sessions.policy is DEFAULT_SESSION_POLICY


# --------------------------------------------------------------------------
# Creation
# --------------------------------------------------------------------------


def test_creation_stamps_every_timestamp_from_the_injected_clock(
    sessions: SessionService,
    user: User,
    clock: FrozenClock,
) -> None:
    created = sessions.create_session(user.user_id)
    session = created.session
    now = parse_utc_timestamp(session.created_at_utc)

    assert session.user_id == user.user_id
    assert session.last_activity_at_utc == session.created_at_utc
    assert parse_utc_timestamp(session.idle_expires_at_utc) == now + timedelta(minutes=60)
    assert parse_utc_timestamp(session.absolute_expires_at_utc) == now + timedelta(hours=8)
    assert session.revoked_at_utc is None


def test_the_raw_token_is_returned_once_and_never_stored(
    sessions: SessionService,
    session_store: SqliteSessionRepository,
    database,
    user: User,
) -> None:
    """The database holds a digest. A stolen copy of it yields no credential."""
    created = sessions.create_session(user.user_id)

    stored = session_store.get_session(created.session.session_id)
    assert stored is not None
    assert stored.token_hash == hash_session_token(created.raw_token)
    assert stored.token_hash != created.raw_token

    # Nothing anywhere in the table equals the raw token.
    rows = database.connect().execute("SELECT * FROM user_sessions").fetchall()
    for row in rows:
        for value in tuple(row):
            assert value != created.raw_token


def test_the_token_hash_is_the_sha256_hex_digest(
    sessions: SessionService,
    user: User,
) -> None:
    import hashlib

    created = sessions.create_session(user.user_id)

    expected = hashlib.sha256(created.raw_token.encode("utf-8")).hexdigest()
    assert created.session.token_hash == expected
    assert len(created.session.token_hash) == 64


def test_the_session_id_is_not_the_token(sessions: SessionService, user: User) -> None:
    """The id is safe to log and to name in an audit record; the token is not.

    Using the token as the primary key would make every reference to a session
    a reference to a live credential.
    """
    created = sessions.create_session(user.user_id)

    assert created.session.session_id.startswith("SES-")
    assert created.session.session_id != created.raw_token
    assert created.raw_token not in created.session.session_id


def test_tokens_are_high_entropy_and_never_repeat(
    sessions: SessionService,
    user: User,
) -> None:
    minted = {sessions.create_session(user.user_id).raw_token for _ in range(25)}

    assert len(minted) == 25
    # token_urlsafe(32) renders 256 bits as ~43 base64url characters.
    assert all(len(token) >= 43 for token in minted)


def test_a_session_carries_no_organization_and_no_role(
    sessions: SessionService,
    user: User,
) -> None:
    """The absence is the design.

    A role captured at login would stay in force for up to eight hours after an
    operator removed it -- exactly the revocation gap sessions exist to close.
    """
    fields = set(type(sessions.create_session(user.user_id).session).__dataclass_fields__)

    assert not {name for name in fields if "organization" in name or "role" in name}


def test_a_user_with_no_membership_still_gets_a_session(
    sessions: SessionService,
    tenants: SqliteTenantRepository,
    user: User,
) -> None:
    assert tenants.list_memberships_for_user(user.user_id) == ()

    assert sessions.create_session(user.user_id).session.user_id == user.user_id


def test_a_disabled_user_cannot_be_given_a_session(
    sessions: SessionService,
    tenants: SqliteTenantRepository,
) -> None:
    disabled = make_user(
        tenants, user_id="USR-test-off", email="off@example.com", status=UserStatus.DISABLED
    )

    with pytest.raises(InactiveUserError):
        sessions.create_session(disabled.user_id)


def test_a_nonexistent_user_cannot_be_given_a_session(sessions: SessionService) -> None:
    with pytest.raises(IdentityNotFoundError):
        sessions.create_session("USR-never-created")


def test_the_created_session_repr_hides_the_raw_token(
    sessions: SessionService,
    user: User,
) -> None:
    """The default dataclass repr is what exception renderers, debuggers and
    structured loggers all reach for. A live bearer credential must not be in
    it."""
    created = sessions.create_session(user.user_id)

    rendered = repr(created)
    assert created.raw_token not in rendered
    assert created.session.token_hash not in rendered
    assert created.session.session_id in rendered


def test_a_created_session_must_agree_with_its_own_token(user: User) -> None:
    """Guards the wrapper: a raw token that does not hash to the stored digest
    would be a session nobody could ever resolve."""
    from identity.sessions import Session

    session = Session(
        session_id="SES-test",
        user_id=user.user_id,
        token_hash=hash_session_token("a-token"),
        created_at_utc="2026-09-12T08:00:00Z",
        last_activity_at_utc="2026-09-12T08:00:00Z",
        idle_expires_at_utc="2026-09-12T09:00:00Z",
        absolute_expires_at_utc="2026-09-12T16:00:00Z",
    )

    with pytest.raises(IdentityValidationError, match="raw_token"):
        CreatedSession(session=session, raw_token="a-different-token")


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def test_a_valid_token_resolves_to_its_session_and_user(
    sessions: SessionService,
    user: User,
) -> None:
    created = sessions.create_session(user.user_id)

    resolved = sessions.resolve_session(created.raw_token)

    assert resolved.session.session_id == created.session.session_id
    assert resolved.user.user_id == user.user_id


@pytest.mark.parametrize("token", ["", "   ", "not-a-real-token", "x" * 43])
def test_an_unknown_or_malformed_token_fails_identically(
    sessions: SessionService,
    user: User,
    token: str,
) -> None:
    """Both are ``SessionNotFoundError``.

    Distinguishing "not a valid token format" from "a valid format matching
    nothing" would confirm to a prober that their guess about the format was
    right.
    """
    sessions.create_session(user.user_id)

    with pytest.raises(SessionNotFoundError):
        sessions.resolve_session(token)


def test_a_token_for_a_session_that_was_never_created_fails(
    sessions: SessionService,
    user: User,
) -> None:
    sessions.create_session(user.user_id)

    with pytest.raises(SessionNotFoundError):
        sessions.resolve_session(new_session_token())


def test_resolution_does_not_renew_activity(
    sessions: SessionService,
    session_store: SqliteSessionRepository,
    user: User,
    clock: FrozenClock,
) -> None:
    """Reads stay reads.

    Renewing inside resolution would turn every authenticated request into a
    write on a single-connection database, and would let a background poll keep
    an abandoned browser logged in forever.
    """
    created = sessions.create_session(user.user_id)
    clock.advance(30 * MINUTE)

    sessions.resolve_session(created.raw_token)

    after = session_store.get_session(created.session.session_id)
    assert after is not None
    assert after.last_activity_at_utc == created.session.last_activity_at_utc
    assert after.idle_expires_at_utc == created.session.idle_expires_at_utc


def test_disabling_a_user_invalidates_their_existing_session(
    sessions: SessionService,
    tenants: SqliteTenantRepository,
    database,
    user: User,
) -> None:
    """Checked on every resolution, not only at creation.

    That is what makes switching off an account take effect on the next
    request instead of whenever that person's session happened to expire.
    """
    created = sessions.create_session(user.user_id)
    database.connect().execute(
        "UPDATE users SET status = 'DISABLED' WHERE user_id = ?", (user.user_id,)
    )

    with pytest.raises(InactiveUserError):
        sessions.resolve_session(created.raw_token)


# --------------------------------------------------------------------------
# Boundaries
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("elapsed", "label"),
    [
        (54 * MINUTE + 59, "just before the warning"),
        (55 * MINUTE, "at the warning threshold"),
        (59 * MINUTE + 59, "one second before idle expiry"),
    ],
)
def test_a_session_is_valid_right_up_to_idle_expiry(
    sessions: SessionService,
    user: User,
    clock: FrozenClock,
    elapsed: int,
    label: str,
) -> None:
    created = sessions.create_session(user.user_id)
    clock.advance(elapsed)

    assert sessions.resolve_session(created.raw_token).session.session_id, label


def test_at_exactly_sixty_minutes_the_session_is_expired(
    sessions: SessionService,
    user: User,
    clock: FrozenClock,
) -> None:
    """Equality means expired.

    ``>=`` rather than ``>``: a session whose idle window ends at exactly this
    instant has ended. Treating the boundary as still-valid would leave a
    one-second window nobody intended and would make "60 minutes" depend on
    clock resolution.
    """
    created = sessions.create_session(user.user_id)
    clock.advance(60 * MINUTE)

    with pytest.raises(SessionExpiredError) as failure:
        sessions.resolve_session(created.raw_token)

    assert failure.value.reason == "idle"


def test_the_warning_threshold_is_not_an_expiry(
    sessions: SessionService,
    user: User,
    clock: FrozenClock,
) -> None:
    """55 minutes is when a frontend should warn, not when the server stops.

    If the two coincided the warning would be pointless -- there would be
    nothing left to continue.
    """
    created = sessions.create_session(user.user_id)
    clock.advance(55 * MINUTE)

    assert sessions.resolve_session(created.raw_token) is not None
    # And five minutes of warning window really do remain.
    clock.advance(int(DEFAULT_SESSION_POLICY.warning_window.total_seconds()) - 1)
    assert sessions.resolve_session(created.raw_token) is not None


def test_an_actively_used_session_still_dies_at_eight_hours(
    sessions: SessionService,
    user: User,
    clock: FrozenClock,
) -> None:
    """The bound that limits a stolen token.

    The session is touched every half hour for the whole eight hours, so idle
    expiry is never reached -- and it ends anyway, exactly on the absolute
    bound.
    """
    created = sessions.create_session(user.user_id)

    for _ in range(15):  # 15 * 30 minutes = 7h30m
        clock.advance(30 * MINUTE)
        sessions.touch_session(created.raw_token)

    clock.advance(29 * MINUTE + 59)  # 7h59m59s
    assert sessions.resolve_session(created.raw_token) is not None

    clock.advance(1)  # exactly 8h
    with pytest.raises(SessionExpiredError) as failure:
        sessions.resolve_session(created.raw_token)

    assert failure.value.reason == "absolute"


def test_absolute_expiry_is_reported_ahead_of_idle_expiry(
    sessions: SessionService,
    user: User,
    clock: FrozenClock,
) -> None:
    """Past both bounds, the absolute one is the honest explanation: the
    session could not have been saved by any amount of activity."""
    created = sessions.create_session(user.user_id)
    clock.advance(9 * HOUR)

    with pytest.raises(SessionExpiredError) as failure:
        sessions.resolve_session(created.raw_token)

    assert failure.value.reason == "absolute"


# --------------------------------------------------------------------------
# Touch
# --------------------------------------------------------------------------


def test_a_touch_moves_activity_and_the_idle_window_forward(
    sessions: SessionService,
    user: User,
    clock: FrozenClock,
) -> None:
    created = sessions.create_session(user.user_id)
    clock.advance(54 * MINUTE)

    touched = sessions.touch_session(created.raw_token)

    expected_activity = parse_utc_timestamp(created.session.created_at_utc) + timedelta(minutes=54)
    assert parse_utc_timestamp(touched.last_activity_at_utc) == expected_activity
    assert parse_utc_timestamp(touched.idle_expires_at_utc) == expected_activity + timedelta(
        minutes=60
    )


def test_a_touch_never_moves_absolute_expiry(
    sessions: SessionService,
    user: User,
    clock: FrozenClock,
) -> None:
    """The single most important property of the absolute bound."""
    created = sessions.create_session(user.user_id)
    original = created.session.absolute_expires_at_utc

    for _ in range(6):
        clock.advance(45 * MINUTE)
        assert sessions.touch_session(created.raw_token).absolute_expires_at_utc == original


def test_a_touch_near_the_end_caps_idle_expiry_at_the_absolute_bound(
    sessions: SessionService,
    user: User,
    clock: FrozenClock,
) -> None:
    """``idle = min(now + 60m, absolute)``.

    Without the cap, activity in the last hour would push the idle window past
    the absolute expiry and the stored row would claim a validity the service
    would then have to override -- a disagreement the schema also refuses.
    """
    created = sessions.create_session(user.user_id)

    # Kept alive to 7h30m, because a session left idle would have died at 60
    # minutes and never reached the region where the cap applies.
    for _ in range(10):
        clock.advance(45 * MINUTE)
        touched = sessions.touch_session(created.raw_token)

    # now + 60m would be 16:30; the absolute bound is 16:00, so it wins.
    assert touched.idle_expires_at_utc == touched.absolute_expires_at_utc
    assert touched.idle_expires_at_utc == created.session.absolute_expires_at_utc
    assert parse_utc_timestamp(touched.idle_expires_at_utc) < parse_utc_timestamp(
        touched.last_activity_at_utc
    ) + timedelta(minutes=60)


def test_a_touch_after_idle_expiry_is_refused(
    sessions: SessionService,
    user: User,
    clock: FrozenClock,
) -> None:
    """Renewal, not resurrection.

    Reviving a session that had already ended would mean a token stayed useful
    after the moment it was supposed to stop working.
    """
    created = sessions.create_session(user.user_id)
    clock.advance(60 * MINUTE)

    with pytest.raises(SessionExpiredError):
        sessions.touch_session(created.raw_token)


def test_a_touch_after_absolute_expiry_is_refused(
    sessions: SessionService,
    user: User,
    clock: FrozenClock,
) -> None:
    created = sessions.create_session(user.user_id)
    for _ in range(16):
        clock.advance(30 * MINUTE)
        if clock() < parse_utc_timestamp(created.session.absolute_expires_at_utc):
            sessions.touch_session(created.raw_token)

    with pytest.raises(SessionExpiredError):
        sessions.touch_session(created.raw_token)


def test_a_touch_on_a_revoked_session_is_refused(
    sessions: SessionService,
    user: User,
    clock: FrozenClock,
) -> None:
    created = sessions.create_session(user.user_id)
    sessions.revoke_session(created.raw_token)
    clock.advance(MINUTE)

    with pytest.raises(SessionRevokedError):
        sessions.touch_session(created.raw_token)


def test_a_touch_on_an_unknown_token_is_refused(sessions: SessionService, user: User) -> None:
    sessions.create_session(user.user_id)

    with pytest.raises(SessionNotFoundError):
        sessions.touch_session(new_session_token())


# --------------------------------------------------------------------------
# Revocation
# --------------------------------------------------------------------------


def test_revocation_makes_the_token_unusable_immediately(
    sessions: SessionService,
    user: User,
) -> None:
    created = sessions.create_session(user.user_id)

    revoked = sessions.revoke_session(created.raw_token)

    assert revoked.revoked_at_utc is not None
    with pytest.raises(SessionRevokedError):
        sessions.resolve_session(created.raw_token)


def test_revocation_is_idempotent_and_keeps_the_first_timestamp(
    sessions: SessionService,
    user: User,
    clock: FrozenClock,
) -> None:
    """The first timestamp is the moment access actually ended; a later call
    did not change that."""
    created = sessions.create_session(user.user_id)
    first = sessions.revoke_session(created.raw_token)

    clock.advance(10 * MINUTE)
    second = sessions.revoke_session(created.raw_token)

    assert second.revoked_at_utc == first.revoked_at_utc


def test_revocation_does_not_delete_the_row(
    sessions: SessionService,
    session_store: SqliteSessionRepository,
    user: User,
) -> None:
    """A revoked session is evidence. Deleting it would destroy the record that
    it was cut short."""
    created = sessions.create_session(user.user_id)

    sessions.revoke_session(created.raw_token)

    stored = session_store.get_session(created.session.session_id)
    assert stored is not None
    assert stored.is_revoked


def test_an_expired_session_can_still_be_revoked(
    sessions: SessionService,
    user: User,
    clock: FrozenClock,
) -> None:
    """Revoking is an instruction about a row, not a request that needs the
    session to still be usable."""
    created = sessions.create_session(user.user_id)
    clock.advance(9 * HOUR)

    assert sessions.revoke_session(created.raw_token).revoked_at_utc is not None


def test_revocation_wins_over_a_later_touch(
    sessions: SessionService,
    user: User,
    clock: FrozenClock,
) -> None:
    created = sessions.create_session(user.user_id)
    clock.advance(MINUTE)
    sessions.revoke_session(created.raw_token)

    clock.advance(MINUTE)
    with pytest.raises(SessionRevokedError):
        sessions.touch_session(created.raw_token)
    with pytest.raises(SessionRevokedError):
        sessions.resolve_session(created.raw_token)


# --------------------------------------------------------------------------
# Expired sessions are kept
# --------------------------------------------------------------------------


def test_expired_sessions_are_not_deleted_by_being_used(
    sessions: SessionService,
    session_store: SqliteSessionRepository,
    user: User,
    clock: FrozenClock,
) -> None:
    """Expiry is a validity decision, not garbage collection.

    Deleting rows during authentication would tie a security decision to
    cleanup and destroy the record that the session ever existed.
    """
    created = sessions.create_session(user.user_id)
    clock.advance(9 * HOUR)

    for _ in range(3):
        with pytest.raises(SessionExpiredError):
            sessions.resolve_session(created.raw_token)

    assert session_store.get_session(created.session.session_id) is not None
