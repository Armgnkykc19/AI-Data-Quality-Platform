"""Typed errors for identity and tenant records.

Kept separate from ``review_application.errors`` on purpose. A caller must be
able to tell "this organization already exists" from "the review queue refused
a decision": the two are produced by different layers, mean different things to
an operator, and will eventually map to different HTTP answers.

Nothing here is an ``Exception`` subclass that persistence invents on its own.
The SQLite tenant repository raises these, exactly as the review repository
raises ``review_application`` errors, so storage never has to define a parallel
error vocabulary of its own.
"""

from __future__ import annotations

__all__ = [
    "DuplicateIdentityError",
    "IdentityError",
    "IdentityNotFoundError",
    "IdentityValidationError",
]


class IdentityError(Exception):
    """Base error for users, organizations, and memberships."""


class IdentityValidationError(IdentityError):
    """A value offered as identity material is not well formed.

    Raised before storage is touched -- an empty display name, a login handle
    that is not an address, an identifier without its type prefix.
    """


class DuplicateIdentityError(IdentityError):
    """A record contradicts one already stored under the same unique key.

    Covers a repeated identifier, a repeated organization slug, a repeated
    normalized login handle, and a second membership for one (organization,
    user) pair. Never raised for a benign re-read: nothing in this layer
    upserts, so a duplicate write is always a genuine conflict.
    """


class IdentityNotFoundError(IdentityError):
    """A referenced user, organization, or membership is not stored."""
