"""Runtime configuration for cookie authentication, and the safe defaults.

Follows the loader shape this project already uses
(``load_review_persistence_config``, ``load_entity_resolution_config``): a
frozen dataclass, eager validation in ``__post_init__``, and a typed
configuration error raised before anything is opened.

Two properties are the whole reason this module is not just three constants.

**``session_cookie_secure`` defaults to True.** A cookie without ``Secure`` is
sent over plain HTTP, so anyone on the path sees a live bearer credential. The
default must therefore be the safe value, and turning it off must be something
an operator writes down -- never something inferred from a hostname, a
``DEBUG`` flag, or the absence of TLS. Silent inference is how a development
default reaches production.

**Insecure mode is structurally confined to loopback.** Writing
``session_cookie_secure: false`` is not enough on its own: every allowed origin
must also be a loopback HTTP origin. So the insecure setting cannot be combined
with a real deployment's origins at all -- a config that tried would fail to
load rather than serve. That turns "please only use this locally" from a
comment into a constraint.

There is no CORS configuration here, deliberately. ``allowed_origins`` is used
only to *reject* unsafe requests whose ``Origin`` is not one of them; nothing in
this API ever sends ``Access-Control-Allow-Origin``. The browser reaches the API
through the Vite same-origin proxy, so there is no cross-origin request to
permit -- only forged ones to refuse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from review_application.errors import ReviewPersistenceConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUTH_HTTP_CONFIG = PROJECT_ROOT / "configs" / "auth_http.yaml"

# Named for this application rather than called "session". A generic name
# collides with whatever else a developer has on localhost, and a collision on
# a cookie name is a collision on a credential.
DEFAULT_SESSION_COOKIE_NAME = "dq_session"

# Schemes whose default port a browser omits from the Origin header. Dropping
# them on both sides is what lets a config written as ``http://127.0.0.1:80``
# match the ``http://127.0.0.1`` a browser actually sends.
_DEFAULT_PORTS = {"http": 80, "https": 443}

_ALLOWED_SCHEMES = frozenset({"http", "https"})

# A cookie name is written into a header, so the character set is the RFC 6265
# token set rather than anything goes.
_COOKIE_NAME_FORBIDDEN = set(' ()<>@,;:\\"/[]?={}\t')


class AuthHttpConfigurationError(ReviewPersistenceConfigurationError):
    """Authentication HTTP configuration is missing, malformed, or unsafe.

    A subclass of the existing configuration error so the one thing every
    caller already does -- refuse to start -- keeps happening, while the type
    still names which configuration was wrong.
    """


def normalize_origin(value: str) -> str:
    """Reduce an origin to the exact form a browser sends, or raise.

    Exact matching needs a canonical form on both sides, so this is applied to
    the configured values *and* to the incoming header. Scheme and host are
    lowercased, and a default port is dropped.

    Everything else is refused rather than trimmed away. An "origin" carrying a
    path, a query, a fragment or credentials is not an origin, and quietly
    discarding the extra part is how ``https://evil.test/@good.test`` becomes a
    match for something.
    """
    if not isinstance(value, str) or not value.strip():
        raise AuthHttpConfigurationError("An origin must be a non-empty string.")
    candidate = value.strip()

    parts = urlsplit(candidate)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise AuthHttpConfigurationError(f"Origin {candidate!r} must use http or https.")
    if parts.path or parts.query or parts.fragment:
        raise AuthHttpConfigurationError(
            f"Origin {candidate!r} must be scheme://host[:port] with no path, query or fragment."
        )
    if parts.username or parts.password:
        raise AuthHttpConfigurationError(f"Origin {candidate!r} must not carry credentials.")
    if not parts.hostname:
        raise AuthHttpConfigurationError(f"Origin {candidate!r} has no host.")

    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    try:
        port = parts.port
    except ValueError as exc:
        raise AuthHttpConfigurationError(f"Origin {candidate!r} has an invalid port.") from exc

    if port is None or port == _DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _is_loopback_origin(origin: str) -> bool:
    """True only for an http origin that cannot be reached from another machine.

    ``ipaddress.is_loopback`` covers all of ``127.0.0.0/8`` and ``::1`` without
    a hand-written list; ``localhost`` is the one name worth accepting and does
    not need a DNS lookup to recognise. Anything else -- including a name that
    happens to resolve to a loopback address today -- is not loopback for this
    purpose, because resolution is not ours to trust.
    """
    parts = urlsplit(origin)
    if parts.scheme != "http":
        return False
    host = (parts.hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class AuthHttpConfig:
    """Everything the HTTP layer needs to handle a session cookie safely."""

    session_cookie_name: str = DEFAULT_SESSION_COOKIE_NAME
    session_cookie_secure: bool = True
    allowed_origins: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        name = self.session_cookie_name
        if not isinstance(name, str) or not name.strip():
            raise AuthHttpConfigurationError("session_cookie_name must be a non-empty string.")
        if set(name) & _COOKIE_NAME_FORBIDDEN:
            raise AuthHttpConfigurationError(
                "session_cookie_name must be an RFC 6265 token: no spaces or separators."
            )
        if not isinstance(self.session_cookie_secure, bool):
            raise AuthHttpConfigurationError("session_cookie_secure must be true or false.")

        object.__setattr__(
            self,
            "allowed_origins",
            tuple(dict.fromkeys(normalize_origin(item) for item in self.allowed_origins)),
        )

        if not self.session_cookie_secure:
            offenders = sorted(
                origin for origin in self.allowed_origins if not _is_loopback_origin(origin)
            )
            if offenders:
                raise AuthHttpConfigurationError(
                    "session_cookie_secure is false, which sends the session cookie over "
                    f"plain HTTP, but these origins are not loopback: {offenders}. The "
                    "insecure setting exists for the documented local runtime only."
                )

    def is_allowed_origin(self, origin: str) -> bool:
        """Exact membership after normalization. No prefixes, no suffixes, no wildcards.

        Substring or ``endswith`` matching is the classic way this check is
        defeated: ``https://good.test.evil.test`` ends with nothing useful, but
        ``https://evil-good.test`` passes a careless ``endswith("good.test")``,
        and ``https://good.test.attacker`` passes a careless ``startswith``.
        Comparing whole normalized origins has no such edge.
        """
        try:
            return normalize_origin(origin) in self.allowed_origins
        except AuthHttpConfigurationError:
            # Not a well-formed origin, so it matches nothing.
            return False


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise AuthHttpConfigurationError(f"Configuration root must be a mapping: {path}")
    return data


def load_auth_http_config(path: Path = DEFAULT_AUTH_HTTP_CONFIG) -> AuthHttpConfig:
    """Read the configured cookie and origin policy, validating eagerly."""
    data = _load_yaml(path)

    raw_origins = data.get("allowed_origins") or ()
    if isinstance(raw_origins, str) or not isinstance(raw_origins, list | tuple):
        raise AuthHttpConfigurationError("allowed_origins must be a list of origins.")

    secure = data.get("session_cookie_secure", True)
    if not isinstance(secure, bool):
        raise AuthHttpConfigurationError(
            "session_cookie_secure must be a boolean; an unrecognised value is not "
            "treated as false."
        )

    return AuthHttpConfig(
        session_cookie_name=str(data.get("session_cookie_name") or DEFAULT_SESSION_COOKIE_NAME),
        session_cookie_secure=secure,
        allowed_origins=tuple(str(item) for item in raw_origins),
    )
