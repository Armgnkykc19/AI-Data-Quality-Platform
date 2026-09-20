"""Who may use this system, and which tenant they belong to.

Sprint 13 Phase B scope: domain models, opaque identifiers, and the two
normalization rules that turn operator-entered text into durable lookup keys.
Nothing here authenticates anybody -- there is no session, no cookie, no
password, no HTTP. Those arrive in the authentication phase and will depend on
this package rather than living inside it.

The boundary this package draws is the one Sprint 13 exists to establish:

* ``identity`` answers *who you are* and *which tenant you are part of*.
* ``review_application`` answers *what may be reviewed*, and owns the
  ``ReviewQueue`` that an organization holds.
* ``human_review`` answers *whether a MATCH is safe*, and knows about neither.

Those are three separate questions, and the packages are kept ignorant of one
another so that answering one can never quietly answer another. In particular,
Sprint 08 MATCH authorization must stay a judgement about review evidence: if
it could see a role, a role could eventually overrule it.
"""

from identity.errors import (
    DuplicateIdentityError,
    IdentityError,
    IdentityNotFoundError,
    IdentityValidationError,
)
from identity.ids import (
    MEMBERSHIP_ID_PREFIX,
    ORGANIZATION_ID_PREFIX,
    USER_ID_PREFIX,
    assert_opaque_id,
    generate_opaque_id,
    new_membership_id,
    new_organization_id,
    new_user_id,
)
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

__all__ = [
    "MEMBERSHIP_ID_PREFIX",
    "ORGANIZATION_ID_PREFIX",
    "USER_ID_PREFIX",
    "DuplicateIdentityError",
    "IdentityError",
    "IdentityNotFoundError",
    "IdentityValidationError",
    "MembershipRole",
    "Organization",
    "OrganizationMembership",
    "OrganizationStatus",
    "User",
    "UserStatus",
    "assert_opaque_id",
    "generate_opaque_id",
    "new_membership_id",
    "new_organization_id",
    "new_user_id",
    "normalize_login_email",
    "normalize_organization_slug",
]
