"""The one rule that turns a typed email address into a lookup key.

Two values are kept for every user, and the distinction is the point.
``email`` is what the operator entered, stored verbatim and shown back
unchanged. ``normalized_email`` is a derived lookup key, and it is the column
carrying the UNIQUE constraint -- so what the database considers "the same
user" and what a future login would look up are the same rule, evaluated by the
same function.

The rule is deliberately minimal:

1. strip surrounding whitespace,
2. reject anything that is not shaped like ``local@domain``,
3. ``str.casefold()``.

``casefold`` rather than ``lower`` because it is the Unicode standard's
caseless-matching operation and maps more spellings together (German ``ß``
folds to ``ss``). For a login key, mapping *more* things together is the safe
direction: two addresses that fold alike become one account rather than two
confusably distinct ones.

What this rule deliberately does **not** do, and why:

* **No dot removal.** ``a.b@example.com`` and ``ab@example.com`` are different
  mailboxes at most providers. Gmail happens to ignore dots; encoding one
  provider's policy would silently merge two distinct users everywhere else.
* **No plus-address stripping.** ``a+tag@`` is a real, deliverable address and
  in some organizations a distinct person's alias.
* **No provider-specific rules of any kind.** The set of providers and their
  policies is not knowable from here and changes without notice.
* **No Unicode NFC/NFKC normalization.** Two canonically equivalent spellings
  of a non-ASCII local part therefore remain distinct keys. This is a known
  limitation, accepted because the alternative -- rewriting the bytes of an
  address -- can change which mailbox an address refers to, and because
  ``casefold`` already covers the case-difference collision that actually
  occurs in practice.
* **No IDN/punycode conversion**, and no RFC 5322 parsing. The shape check
  below is a gate against obvious nonsense reaching a durable unique key, not
  a claim that the address is valid or deliverable.

The consequence to state plainly: this function can return the same key for two
addresses only through case, and can return different keys for two addresses
that a particular mail provider would deliver to one mailbox. The second is a
duplicate account, which an operator can see and fix. The first direction --
silently merging two people -- is the one that cannot be undone, and this rule
is chosen so it cannot happen outside case folding.
"""

from __future__ import annotations

from identity.errors import IdentityValidationError

__all__ = ["MAX_EMAIL_LENGTH", "normalize_login_email"]

# A bound on a value that becomes a durable unique key. Well above the 254
# octets SMTP allows for a forward path, so no deliverable address is refused
# by length alone.
MAX_EMAIL_LENGTH = 320


def normalize_login_email(value: object) -> str:
    """Return the lookup key for a login handle, or raise.

    The returned value is what ``users.normalized_email`` stores and what any
    lookup must be performed against. The caller keeps the entered spelling
    separately; this function never sees it again.
    """
    if not isinstance(value, str):
        raise IdentityValidationError(f"email must be a string; got {type(value).__name__}.")

    stripped = value.strip()
    if not stripped:
        raise IdentityValidationError("email must not be empty.")
    if len(stripped) > MAX_EMAIL_LENGTH:
        raise IdentityValidationError(f"email exceeds {MAX_EMAIL_LENGTH} characters.")
    if any(character.isspace() for character in stripped):
        raise IdentityValidationError("email must not contain whitespace.")

    # rsplit on a single "@" that must be the only one: a local part may not
    # contain an unquoted "@", and quoted local parts are outside what this
    # gate claims to support.
    if stripped.count("@") != 1:
        raise IdentityValidationError("email must contain exactly one '@'.")
    local, domain = stripped.split("@")
    if not local or not domain:
        raise IdentityValidationError("email must have a non-empty local part and domain.")
    if "." not in domain:
        raise IdentityValidationError("email domain must contain a dot.")

    return stripped.casefold()
