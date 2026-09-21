"""Argon2id hashing: the algorithm, the policy, and what never leaks.

The properties here are the ones every other authentication test assumes
without re-checking -- that the stored verifier really is Argon2id, that two
hashes of one password differ, that a corrupt hash refuses rather than
accepts, and that a rejected password never appears anywhere.
"""

from __future__ import annotations

import pytest

from identity.credentials import DEFAULT_PASSWORD_POLICY, PasswordCredential, PasswordPolicy
from identity.errors import IdentityValidationError
from identity.passwords import Argon2idPasswordHasher
from tests.identity.conftest import PASSWORD, cheap_hasher

# --------------------------------------------------------------------------
# The algorithm
# --------------------------------------------------------------------------


def test_the_encoded_hash_is_argon2id() -> None:
    """Not Argon2i, not Argon2d, and not something else entirely.

    Argon2id is the hybrid RFC 9106 recommends for password hashing: it
    inherits Argon2i's side-channel resistance and Argon2d's resistance to
    GPU time-memory tradeoffs. Asserting the variant in the encoded string is
    the only way to notice if a future edit changed the type parameter.
    """
    encoded = cheap_hasher().hash(PASSWORD)

    assert encoded.startswith("$argon2id$")


def test_the_same_password_hashes_differently_every_time() -> None:
    """A fresh random salt per hash, which is what makes the digests unequal.

    Without it, two people who chose the same password would have identical
    stored verifiers -- visible to anyone who read the table, and precomputable
    across every installation.
    """
    hasher = cheap_hasher()

    first = hasher.hash(PASSWORD)
    second = hasher.hash(PASSWORD)

    assert first != second
    assert hasher.verify(first, PASSWORD)
    assert hasher.verify(second, PASSWORD)


def test_the_correct_password_verifies_and_a_wrong_one_does_not() -> None:
    hasher = cheap_hasher()
    encoded = hasher.hash(PASSWORD)

    assert hasher.verify(encoded, PASSWORD) is True
    assert hasher.verify(encoded, PASSWORD + "x") is False
    assert hasher.verify(encoded, "") is False


def test_verification_is_exact_about_whitespace_and_unicode() -> None:
    """The hasher sees exactly the bytes supplied, with nothing normalised.

    A trimmed or case-folded password would be a different secret, and the
    transformation would have to be reproduced identically forever -- including
    against hashes written before it existed.
    """
    hasher = cheap_hasher()
    spaced = "  spaced password  "
    accented = "parolamı unuttum éçö"

    assert hasher.verify(hasher.hash(spaced), spaced) is True
    assert hasher.verify(hasher.hash(spaced), spaced.strip()) is False
    assert hasher.verify(hasher.hash(accented), accented) is True


@pytest.mark.parametrize(
    "stored",
    ["", "not-a-hash", "$argon2id$", "$argon2id$v=19$m=8,t=1,p=1$bogus", "$2b$12$abcdefgh"],
)
def test_a_malformed_stored_hash_refuses_rather_than_accepts(stored: str) -> None:
    """A row nobody can parse authenticates nobody.

    The safe reading of a corrupt verifier is "this does not let anyone in",
    never "let them in". The library raises three different exceptions across
    these inputs; all of them collapse to False rather than escaping to a
    caller that might render the algorithm and the stored value in a message.
    """
    assert cheap_hasher().verify(stored, PASSWORD) is False


def test_needs_rehash_reports_a_hash_made_with_weaker_parameters() -> None:
    """The upgrade signal, tested by actually moving the parameters.

    A hash produced at time_cost=1 is not one a hasher configured for
    time_cost=3 would produce, so the stronger hasher asks for it to be
    replaced -- which is what lets a successful login quietly upgrade a
    credential.
    """
    weak = Argon2idPasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    strong = Argon2idPasswordHasher(time_cost=3, memory_cost=16, parallelism=1)
    encoded = weak.hash(PASSWORD)

    assert weak.needs_rehash(encoded) is False
    assert strong.needs_rehash(encoded) is True
    # The weaker hash still verifies. Rehashing is an upgrade, never a reason
    # to reject a password that is correct.
    assert strong.verify(encoded, PASSWORD) is True


def test_needs_rehash_says_no_to_a_hash_it_cannot_parse() -> None:
    """An unparseable hash verified nobody, so there is nothing to replace.

    Answering True would invite a caller to overwrite a credential row based
    on a value it failed to understand.
    """
    assert cheap_hasher().needs_rehash("not-a-hash") is False


def test_production_parameters_are_the_librarys_own() -> None:
    """Test speed is bought by injection, never by weakening the default.

    Constructing the hasher with no arguments must yield argon2-cffi's current
    parameters, which track the RFC 9106 profiles and rise over time. Pinning
    numbers in this project would freeze it at whatever was current the day it
    was written.
    """
    from argon2 import PasswordHasher as LibraryHasher

    library = LibraryHasher()
    ours = Argon2idPasswordHasher()._hasher

    assert (ours.time_cost, ours.memory_cost, ours.parallelism) == (
        library.time_cost,
        library.memory_cost,
        library.parallelism,
    )
    # And the cheap test hasher is genuinely weaker, so the assertion above is
    # not trivially true.
    assert cheap_hasher()._hasher.memory_cost < library.memory_cost


# --------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------


def test_the_default_policy_is_twelve_to_two_hundred_fifty_six() -> None:
    assert DEFAULT_PASSWORD_POLICY.min_length == 12
    assert DEFAULT_PASSWORD_POLICY.max_length == 256


def test_a_password_at_the_minimum_length_is_accepted() -> None:
    """Twelve characters exactly. The boundary is inclusive."""
    assert DEFAULT_PASSWORD_POLICY.assert_allowed("a" * 12) == "a" * 12


def test_a_password_one_character_short_is_rejected() -> None:
    with pytest.raises(IdentityValidationError, match="at least 12"):
        DEFAULT_PASSWORD_POLICY.assert_allowed("a" * 11)


def test_a_password_at_and_over_the_maximum() -> None:
    assert DEFAULT_PASSWORD_POLICY.assert_allowed("a" * 256)

    with pytest.raises(IdentityValidationError, match="at most 256"):
        DEFAULT_PASSWORD_POLICY.assert_allowed("a" * 257)


def test_the_policy_returns_the_password_untouched() -> None:
    """No trim, no fold, no normalisation -- the returned value is identical.

    This is what the hasher receives, so any transformation here would silently
    become part of the stored secret.
    """
    padded = "   " + "a" * 12 + "   "

    assert DEFAULT_PASSWORD_POLICY.assert_allowed(padded) == padded


def test_a_surrounding_space_counts_toward_the_length() -> None:
    """Because it is part of the password, not decoration around it.

    A policy that stripped first would accept an 11-character password wrapped
    in spaces, and would then hash something the person did not type.
    """
    assert DEFAULT_PASSWORD_POLICY.assert_allowed(" " * 6 + "abcdef") == " " * 6 + "abcdef"


def test_unicode_is_counted_in_characters_not_bytes() -> None:
    """Twelve emoji are twelve characters, though they are far more bytes.

    Counting UTF-8 bytes would make the minimum mean different things to
    different alphabets, and would quietly reward non-ASCII passwords.
    """
    twelve_wide = "é" * 12

    assert len(twelve_wide.encode("utf-8")) > 12
    assert DEFAULT_PASSWORD_POLICY.assert_allowed(twelve_wide) == twelve_wide


def test_a_password_containing_nul_is_rejected() -> None:
    """A NUL truncates the secret at any C string boundary it crosses.

    Two different passwords sharing a prefix before the NUL could otherwise
    hash to the same verifier.
    """
    with pytest.raises(IdentityValidationError, match="NUL"):
        DEFAULT_PASSWORD_POLICY.assert_allowed("abcdef\x00ghijkl")


@pytest.mark.parametrize("value", [None, 12, b"a" * 12, ["a"] * 12])
def test_a_non_string_password_is_rejected(value: object) -> None:
    with pytest.raises(IdentityValidationError, match="string"):
        DEFAULT_PASSWORD_POLICY.assert_allowed(value)


def test_the_rejected_password_never_appears_in_the_error() -> None:
    """An exception message carrying a password is a password in a log file."""
    secret = "sup3rsecret"

    with pytest.raises(IdentityValidationError) as failure:
        DEFAULT_PASSWORD_POLICY.assert_allowed(secret)

    assert secret not in str(failure.value)
    assert secret not in repr(failure.value)


def test_there_are_no_composition_rules() -> None:
    """Length is the only requirement, deliberately.

    Composition rules ("one uppercase, one digit, one symbol") push people
    toward predictable shapes like ``Password1!`` and measurably reduce
    entropy rather than adding it.
    """
    for candidate in ("aaaaaaaaaaaa", "111111111111", "            ", "é" * 12):
        assert DEFAULT_PASSWORD_POLICY.assert_allowed(candidate) == candidate


def test_a_policy_with_an_impossible_range_is_refused() -> None:
    with pytest.raises(IdentityValidationError):
        PasswordPolicy(min_length=0)
    with pytest.raises(IdentityValidationError):
        PasswordPolicy(min_length=12, max_length=8)


# --------------------------------------------------------------------------
# The credential record
# --------------------------------------------------------------------------


def test_the_credential_repr_hides_the_hash() -> None:
    """Reprs end up in exception renderings, debuggers and log lines.

    An Argon2id verifier is not a password, but it is still the most valuable
    thing in the table, and a record that prints it will eventually print it
    somewhere it outlives the process.
    """
    credential = PasswordCredential(
        user_id="USR-test-ada",
        password_hash="$argon2id$v=19$m=8,t=1,p=1$c2FsdHNhbHQ$aGFzaA",
        created_at_utc="2026-09-12T08:00:00Z",
        updated_at_utc="2026-09-12T08:00:00Z",
    )

    rendered = repr(credential)

    assert "argon2id" not in rendered
    assert "USR-test-ada" in rendered
