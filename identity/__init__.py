"""Who may use this system, which tenant they belong to, and how they prove it.

Phase B established the tenant graph: users, organizations, memberships and the
identifiers and normalization rules behind them. Phase C added the
authentication core beneath it -- Argon2id password credentials, opaque
server-side sessions, and the services that turn a password into a verified
identity and a verified identity into a session.

**Still no HTTP.** There is no login endpoint, no cookie, no FastAPI
dependency, and nothing in the browser. Those arrive in a later phase and will
depend on this package rather than living inside it. Building the
security-sensitive core first means it can be proven against a deterministic
clock and in-memory fakes, before any of it is reachable over a socket.

The boundary this package draws is the one Sprint 13 exists to establish:

* ``identity`` answers *who you are* -- and, through membership, *which tenant
  you are part of*.
* ``review_application`` answers *what may be reviewed*, and owns the
  ``ReviewQueue`` an organization holds.
* ``human_review`` answers *whether a MATCH is safe*, and knows about neither.

Those are three separate questions, and the packages are kept ignorant of one
another so that answering one can never quietly answer another. In particular
Sprint 08 MATCH authorization must stay a judgement about review evidence: if
it could see a role, a role could eventually overrule it.

The same separation runs through authentication itself. A session establishes
identity and carries no organization and no role, so authorization reads
current membership at the moment of each request -- which is what lets removing
someone's access take effect immediately rather than whenever their session
happened to expire.
"""

from identity.authentication import AuthenticatedUser, AuthenticationService
from identity.clock import (
    Clock,
    format_utc_timestamp,
    parse_utc_timestamp,
    system_utc_now,
    utc_now,
    utc_timestamp,
)
from identity.credentials import (
    DEFAULT_PASSWORD_POLICY,
    PasswordCredential,
    PasswordPolicy,
)
from identity.errors import (
    AuthenticationError,
    AuthenticationFailedError,
    CredentialNotFoundError,
    DuplicateIdentityError,
    IdentityError,
    IdentityNotFoundError,
    IdentityValidationError,
    InactiveUserError,
    PasswordHashingError,
    SessionError,
    SessionExpiredError,
    SessionNotFoundError,
    SessionRevokedError,
)
from identity.ids import (
    MEMBERSHIP_ID_PREFIX,
    ORGANIZATION_ID_PREFIX,
    SESSION_ID_PREFIX,
    USER_ID_PREFIX,
    assert_opaque_id,
    generate_opaque_id,
    new_membership_id,
    new_organization_id,
    new_session_id,
    new_user_id,
)
from identity.login import LoginResult, LoginService
from identity.models import (
    MembershipRole,
    Organization,
    OrganizationMembership,
    OrganizationStatus,
    User,
    UserStatus,
    normalize_organization_slug,
)
from identity.normalization import normalize_login_email
from identity.passwords import Argon2idPasswordHasher, PasswordHasher
from identity.provisioning import PasswordProvisioningService
from identity.repository import (
    CredentialRepository,
    SessionRepository,
    UserLookup,
    UserReader,
)
from identity.session_service import ResolvedSession, SessionService
from identity.sessions import (
    DEFAULT_SESSION_POLICY,
    CreatedSession,
    Session,
    SessionPolicy,
    hash_session_token,
    new_session_token,
)

__all__ = [
    "DEFAULT_PASSWORD_POLICY",
    "DEFAULT_SESSION_POLICY",
    "MEMBERSHIP_ID_PREFIX",
    "ORGANIZATION_ID_PREFIX",
    "SESSION_ID_PREFIX",
    "USER_ID_PREFIX",
    "Argon2idPasswordHasher",
    "AuthenticatedUser",
    "AuthenticationError",
    "AuthenticationFailedError",
    "AuthenticationService",
    "Clock",
    "CreatedSession",
    "CredentialNotFoundError",
    "CredentialRepository",
    "DuplicateIdentityError",
    "IdentityError",
    "IdentityNotFoundError",
    "IdentityValidationError",
    "InactiveUserError",
    "LoginResult",
    "LoginService",
    "MembershipRole",
    "Organization",
    "OrganizationMembership",
    "OrganizationStatus",
    "PasswordCredential",
    "PasswordHasher",
    "PasswordHashingError",
    "PasswordPolicy",
    "PasswordProvisioningService",
    "ResolvedSession",
    "Session",
    "SessionError",
    "SessionExpiredError",
    "SessionNotFoundError",
    "SessionPolicy",
    "SessionRepository",
    "SessionRevokedError",
    "SessionService",
    "User",
    "UserLookup",
    "UserReader",
    "UserStatus",
    "assert_opaque_id",
    "format_utc_timestamp",
    "generate_opaque_id",
    "hash_session_token",
    "new_membership_id",
    "new_organization_id",
    "new_session_id",
    "new_session_token",
    "new_user_id",
    "normalize_login_email",
    "normalize_organization_slug",
    "parse_utc_timestamp",
    "system_utc_now",
    "utc_now",
    "utc_timestamp",
]
