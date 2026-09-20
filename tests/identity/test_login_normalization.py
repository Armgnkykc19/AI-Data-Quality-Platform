"""The one rule that turns a typed address into a login key, and its limits.

This function decides what "the same user" means, for the UNIQUE constraint on
``users.normalized_email`` and for the login lookup a later phase will write.
The two must agree, so the rule is pinned here -- including the things it
deliberately does not do, because each of those is a way two distinct mailboxes
could otherwise be merged into one account.
"""

from __future__ import annotations

import unicodedata

import pytest

from identity.errors import IdentityValidationError
from identity.normalization import MAX_EMAIL_LENGTH, normalize_login_email

# --------------------------------------------------------------------------
# What the rule does
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entered", "expected"),
    [
        ("ada@example.com", "ada@example.com"),
        ("Ada@Example.Com", "ada@example.com"),
        ("  ada@example.com  ", "ada@example.com"),
        ("ADA@EXAMPLE.COM", "ada@example.com"),
        ("\tada@example.com\n", "ada@example.com"),
    ],
)
def test_surrounding_whitespace_and_case_are_folded(entered: str, expected: str) -> None:
    assert normalize_login_email(entered) == expected


def test_casefold_rather_than_lower_is_used() -> None:
    """``casefold`` maps more spellings together than ``lower`` does.

    For a login key that is the safe direction: two addresses that fold alike
    become one account rather than two confusably distinct ones. German sharp s
    is the standard example -- ``lower`` leaves it alone, ``casefold`` does not.
    """
    assert normalize_login_email("STRASSE@example.com") == "strasse@example.com"
    assert normalize_login_email("straße@example.com") == "strasse@example.com"


def test_the_rule_is_idempotent() -> None:
    once = normalize_login_email("Ada@Example.com")

    assert normalize_login_email(once) == once


# --------------------------------------------------------------------------
# What the rule deliberately does NOT do
# --------------------------------------------------------------------------


def test_dots_in_the_local_part_are_preserved() -> None:
    """Gmail ignores dots; most providers do not.

    Encoding one provider's policy here would silently merge two distinct
    users everywhere else -- and merging two people is the one direction that
    cannot be undone.
    """
    assert normalize_login_email("a.b@example.com") != normalize_login_email("ab@example.com")


def test_plus_addressing_is_preserved() -> None:
    """``a+tag@`` is a real, deliverable address and in some organizations an alias."""
    assert normalize_login_email("ada+work@example.com") == "ada+work@example.com"
    assert normalize_login_email("ada+work@example.com") != normalize_login_email("ada@example.com")


def test_no_provider_specific_rule_is_applied() -> None:
    """Gmail and googlemail are not folded together, and neither is anything else."""
    assert normalize_login_email("ada@gmail.com") != normalize_login_email("ada@googlemail.com")


def test_unicode_composition_is_not_normalized() -> None:
    """A documented limitation, accepted rather than hidden.

    Two canonically equivalent spellings of a non-ASCII local part remain
    distinct keys. The alternative -- rewriting the bytes of an address -- can
    change which mailbox the address refers to, which is worse than a duplicate
    account an operator can see and fix.
    """
    # Built with unicodedata rather than typed literally, so an editor that
    # normalizes this source file cannot quietly make the test vacuous.
    composed = unicodedata.normalize("NFC", "éve@example.com")
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed

    assert normalize_login_email(composed) != normalize_login_email(decomposed)


# --------------------------------------------------------------------------
# The shape gate
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "no-at-sign",
        "@example.com",
        "ada@",
        "two@@example.com",
        "ada@localhost",
        "ada bee@example.com",
        "ada@exa mple.com",
    ],
)
def test_values_that_are_not_shaped_like_an_address_are_refused(value: str) -> None:
    with pytest.raises(IdentityValidationError):
        normalize_login_email(value)


def test_a_non_string_is_refused() -> None:
    with pytest.raises(IdentityValidationError, match="string"):
        normalize_login_email(None)


def test_an_over_long_address_is_refused() -> None:
    """The value becomes a durable unique key, so it is bounded.

    The cap is well above the 254 octets SMTP allows for a forward path, so no
    deliverable address is refused by length alone.
    """
    assert MAX_EMAIL_LENGTH > 254

    with pytest.raises(IdentityValidationError, match="characters"):
        normalize_login_email("a" * MAX_EMAIL_LENGTH + "@example.com")
