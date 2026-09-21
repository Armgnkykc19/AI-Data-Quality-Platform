"""The official way to run the reviewer API locally: ``python -m review_api``.

There is exactly one supported command, and it starts one thing: the
application ``create_production_app`` builds, with the lifespan that owns the
configured review queue. Nothing here constructs a repository, reads a database,
or decides anything about a review case.

Three defaults are policy rather than preference.

``127.0.0.1`` because this API has no authentication, no verified reviewer
identity, and no tenant isolation, while ``POST .../resolve`` is an
authoritative write over customer-derived evidence. Binding it to ``0.0.0.0``
would publish an unauthenticated decision endpoint to the network, so the host
is validated rather than trusted, and a non-loopback address is refused here
instead of failing later at a firewall that may not exist.

``workers=1`` because Sprint 10's ``ReviewDatabase`` holds a single ``sqlite3``
connection, and a ``sqlite3`` connection is legal only on the thread that
created it. A second worker is a second process with its own connection to the
same file, which is not what the optimistic-version contract was designed
against. Sprint 14 owns the concurrency model.

``reload=False`` because the reloader runs the application in a child process
and restarts it on file changes, which would reopen the queue underneath a
request. It is a development convenience this queue cannot afford.

The database path is deliberately not a flag. The queue location is operator
configuration, read from ``configs/review_persistence.yaml`` by the lifespan; a
flag here could only disagree with what the lifespan actually opens.
"""

from __future__ import annotations

import argparse
import ipaddress
from collections.abc import Sequence

import uvicorn

from review_api.app import create_production_app
from review_api.dependencies import configured_database_path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

# One worker, always. Exposed as a constant so the reason above has a single
# place to point at, and so a test can assert what is handed to the server.
WORKERS = 1

# The lowest and highest values a TCP port can take. argparse gives us an int;
# this is what makes it a port.
MIN_PORT = 1
MAX_PORT = 65535

# Both steps, because registration names a tenant that must already exist and
# will not be created for the operator. Printing only the second line would
# send them into an error they have no obvious way to resolve.
BOOTSTRAP_COMMAND = (
    "python scripts/manage_human_review.py create-organization --slug <slug> --name <name>; "
    "python scripts/manage_human_review.py generate <input> "
    "--report-dir <dir> --register-review-queue --organization <slug>"
)

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NOT_BOOTSTRAPPED = 2


def is_loopback(host: str) -> bool:
    """True only for an address that cannot be reached from another machine.

    ``ipaddress`` is what does the work: ``is_loopback`` covers all of
    ``127.0.0.0/8`` and ``::1`` without a hand-written list, and it is false for
    ``0.0.0.0`` and ``::``, which are the two spellings that actually publish a
    socket to every interface.

    A name that is not an IP literal is refused rather than resolved. Resolving
    it would mean a DNS lookup deciding whether this API is reachable from the
    network, and ``localhost`` -- the one name worth supporting -- does not need
    one.
    """
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m review_api",
        description="Serve the reviewer API for the persistent review queue on localhost.",
        epilog=(
            "Localhost only: this API has no authentication and no reviewer identity. "
            "Register a review queue before starting it. "
            "Exit codes: 0 success, 1 usage, 2 no review queue registered."
        ),
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Loopback address to bind. Default: {DEFAULT_HOST}.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to bind. Default: {DEFAULT_PORT}.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    if not is_loopback(args.host):
        print(
            f"Refusing to bind {args.host!r}: the Sprint 11 reviewer API is "
            "localhost only. It has no authentication, no verified reviewer "
            "identity, and an authoritative write endpoint. Use 127.0.0.1, "
            "localhost, or ::1."
        )
        return EXIT_USAGE
    if not MIN_PORT <= args.port <= MAX_PORT:
        print(f"Port {args.port} is outside {MIN_PORT}-{MAX_PORT}.")
        return EXIT_USAGE

    database_path = configured_database_path()
    if not database_path.exists():
        # Starting anyway would create the file, initialize an empty schema, and
        # serve an empty queue that looks exactly like a reviewed-out one.
        print(f"No review queue database at {database_path}.")
        print(f"Register one first: {BOOTSTRAP_COMMAND}")
        return EXIT_NOT_BOOTSTRAPPED

    print(f"Review queue: {database_path}")
    print(f"Serving the reviewer API on http://{args.host}:{args.port} (localhost only).")
    uvicorn.run(
        create_production_app(),
        host=args.host,
        port=args.port,
        workers=WORKERS,
        reload=False,
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
