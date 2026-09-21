#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from entity_resolution.config import load_entity_resolution_config  # noqa: E402
from entity_resolution.engine import resolve_entities  # noqa: E402
from entity_resolution.models import EntityRecord, ResolutionResult  # noqa: E402
from entity_resolution.records import build_entity_records_from_quality_result  # noqa: E402
from human_review.cases import generate_review_cases  # noqa: E402
from human_review.errors import HumanReviewError, HumanReviewReportError  # noqa: E402
from human_review.models import HumanReviewDecision, ReviewWorkflowState  # noqa: E402
from human_review.reporting import (  # noqa: E402
    load_human_review_report,
    write_review_reports,
)
from human_review.workflow import ReviewWorkflow  # noqa: E402
from identity.errors import (  # noqa: E402
    IdentityError,
    IdentityNotFoundError,
    IdentityValidationError,
)
from identity.models import MembershipRole, Organization, OrganizationMembership  # noqa: E402
from identity.passwords import Argon2idPasswordHasher  # noqa: E402
from identity.provisioning import UserProvisioningService  # noqa: E402
from ingestion.config import load_ingestion_config  # noqa: E402
from ingestion.errors import IngestionError  # noqa: E402
from ingestion.parser import parse_file  # noqa: E402
from record_quality.pipeline import run_quality_pipeline  # noqa: E402
from review_application import (  # noqa: E402
    ReviewApplicationError,
    ReviewQueue,
    ReviewWorkflowRegistration,
    register_review_workflow,
)

# This command is a composition root, so naming the concrete backend here is the
# point rather than a leak: it is the one place that decides the durable queue is
# the Sprint 10 SQLite database. ``register_review_workflow`` above it only ever
# sees the repository Protocol.
from review_persistence import load_review_persistence_config  # noqa: E402
from review_persistence.integrity import verify_review_queue  # noqa: E402
from review_persistence.sqlite import (  # noqa: E402
    ReviewDatabase,
    SqliteReviewCaseRepository,
    SqliteTenantRepository,
    SqliteUserProvisioningRepository,
    open_review_database,
)

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_INGESTION = 2
EXIT_REPORT = 3
EXIT_POLICY = 4
EXIT_REGISTRATION_REFUSED = 5
EXIT_TENANT_REFUSED = 6
# A queue that was found and read successfully, and is not coherent. Distinct
# from every code above because nothing was refused and nothing failed: the
# command did exactly what it was asked and the answer is bad news.
EXIT_INTEGRITY_FAILED = 7


def _add_review_db_argument(parser: argparse.ArgumentParser) -> None:
    """The one way any subcommand names a database other than the configured one."""
    parser.add_argument(
        "--review-db",
        type=Path,
        default=None,
        help=(
            "Override the review queue database path. Defaults to the path in "
            "configs/review_persistence.yaml."
        ),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate and resolve deterministic human review cases for entity resolution.",
        epilog=(
            "Exit codes: 0 success, 1 usage, 2 ingestion, "
            "3 report/IO error, 4 human-review policy rejection, "
            "5 review queue registration refused, 6 organization/tenant refused, "
            "7 queue integrity findings."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Tenant provisioning. Separate commands rather than flags on `generate`,
    # because creating a tenant is a decision an operator makes once and
    # deliberately -- not something that should happen as a side effect of
    # registering a workflow with a mistyped slug.
    create_organization_parser = subparsers.add_parser(
        "create-organization",
        help="Create an organization that can own review queues.",
    )
    create_organization_parser.add_argument("--slug", type=str, required=True)
    create_organization_parser.add_argument("--name", type=str, required=True)
    _add_review_db_argument(create_organization_parser)

    list_organizations_parser = subparsers.add_parser(
        "list-organizations", help="List the organizations in the review database."
    )
    _add_review_db_argument(list_organizations_parser)

    # A login-capable person. Deliberately no --password: an argument is
    # visible in shell history and in the process list of every other user on
    # the machine, which are two places a credential must never be.
    create_user_parser = subparsers.add_parser(
        "create-user",
        help="Create a user who can sign in. Prompts for the password.",
    )
    create_user_parser.add_argument("--email", type=str, required=True)
    create_user_parser.add_argument("--display-name", type=str, required=True)
    _add_review_db_argument(create_user_parser)

    # Joining a user to an organization in one role. Every part is explicit and
    # nothing is inferred: no default role, no "the only organization", and no
    # creation of either side. A membership is what makes a tenant's review data
    # reachable at all, so granting one is an operator decision that must be
    # typed out in full.
    #
    # There is no HTTP counterpart to this command and there must not be. An
    # endpoint that granted memberships would be an endpoint that grants access
    # to customer review evidence, reachable with a stolen cookie.
    add_membership_parser = subparsers.add_parser(
        "add-membership",
        help="Grant an existing user a role in an existing organization.",
    )
    add_membership_parser.add_argument("--user", type=str, required=True)
    add_membership_parser.add_argument("--organization", type=str, required=True)
    add_membership_parser.add_argument(
        "--role",
        type=str,
        choices=[role.value for role in MembershipRole],
        required=True,
        help="VIEWER may read the queue; REVIEWER may also record decisions.",
    )
    _add_review_db_argument(add_membership_parser)

    create_queue_parser = subparsers.add_parser(
        "create-review-queue",
        help="Create a review queue inside an existing organization.",
    )
    create_queue_parser.add_argument("--organization", type=str, required=True)
    create_queue_parser.add_argument("--name", type=str, required=True)
    _add_review_db_argument(create_queue_parser)

    # Verify, never repair. The queue is named the same way every other queue
    # operation names one -- organization slug plus queue name -- so an operator
    # does not have to know an opaque id to check their own data.
    verify_queue_parser = subparsers.add_parser(
        "verify-queue",
        help="Check one review queue's integrity. Read-only; repairs nothing.",
        description=(
            "Reports whether a review queue is coherent: its ownership, its expected "
            "indexes, the queue-global resolution_sequence invariant, its history, and "
            "whether the Sprint 08 authorization bundle can be reconstructed. It writes "
            "nothing, renumbers nothing, and fixes nothing."
        ),
    )
    verify_queue_parser.add_argument("--organization", type=str, required=True)
    verify_queue_parser.add_argument("--review-queue", type=str, required=True)
    _add_review_db_argument(verify_queue_parser)

    generate_parser = subparsers.add_parser(
        "generate", help="Generate review cases from input data."
    )
    generate_parser.add_argument("input_path", type=Path)
    generate_parser.add_argument("--report-dir", type=Path, required=True)
    # Opt-in, never implicit. Writing the report is a read-only artifact; writing
    # the durable queue is what a reviewer will later act on, so it takes an
    # explicit operator decision rather than happening on every generate run.
    generate_parser.add_argument(
        "--register-review-queue",
        action="store_true",
        help="Also register the generated workflow into the durable review queue.",
    )
    # Neither the tenant nor the queue is ever inferred, and this command
    # creates neither. Both must already exist, named explicitly, because
    # provisioning a tenant resource and generating a workflow are two
    # different operator decisions: a missing argument or a misspelled name
    # must fail, not manufacture a queue nobody asked for.
    #
    # Declared without `required=True` because they are required only alongside
    # --register-review-queue, which argparse cannot express; the check lives
    # in main() and produces one message covering both.
    generate_parser.add_argument(
        "--organization",
        type=str,
        default=None,
        help="Slug of the organization that owns the queue. Required with --register-review-queue.",
    )
    generate_parser.add_argument(
        "--review-queue",
        type=str,
        default=None,
        help=(
            "Name of an existing review queue within the organization. Required with "
            "--register-review-queue. Create it first with 'create-review-queue'."
        ),
    )
    _add_review_db_argument(generate_parser)

    list_parser = subparsers.add_parser("list", help="List review cases from a saved report.")
    list_parser.add_argument("report_path", type=Path)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect one review case.")
    inspect_parser.add_argument("report_path", type=Path)
    inspect_parser.add_argument("review_case_id", type=str)

    # Retained only to refuse. This subcommand used to resolve a review case
    # inside a JSON report and write a new report, which made the report a
    # second authoritative record of human decisions alongside the SQLite queue
    # -- and the two never learned about each other. A NO_MATCH recorded here
    # was invisible to the queue, so it did not constrain a later MATCH the way
    # Sprint 08 transitive safety says a NO_MATCH must.
    #
    # It keeps its original arguments so an existing invocation still parses and
    # gets an explanation naming the authoritative path, rather than argparse's
    # "invalid choice" for a command that used to work. It never resolves
    # anything and never writes a report.
    resolve_parser = subparsers.add_parser(
        "resolve",
        help="Removed. Resolve through the authenticated review API instead.",
        description=(
            "Removed in Sprint 14 Phase A. Resolving inside a JSON report made the "
            "report a second authoritative store of human decisions, invisible to the "
            "durable review queue that Sprint 08 authorization is evaluated against."
        ),
    )
    resolve_parser.add_argument("report_path", type=Path)
    resolve_parser.add_argument("review_case_id", type=str)
    resolve_parser.add_argument(
        "--decision",
        choices=[item.value for item in HumanReviewDecision],
        required=True,
    )
    resolve_parser.add_argument("--reviewer-id", type=str, default=None)
    resolve_parser.add_argument("--output-report-dir", type=Path, required=True)

    parser.add_argument(
        "--ingestion-config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "ingestion.yaml",
    )
    parser.add_argument(
        "--entity-resolution-config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "entity_resolution.yaml",
    )
    return parser.parse_args()


def _load_records(input_path: Path, ingestion_config) -> list[EntityRecord]:
    parsed = parse_file(input_path, config=ingestion_config)
    quality = run_quality_pipeline(parsed)
    return build_entity_records_from_quality_result(parsed, quality)


def _open_review_queue(review_db: Path | None) -> tuple[ReviewDatabase, Path]:
    """Open the configured review queue, or the one the operator named.

    The path is runtime configuration and comes from the YAML config or this
    command line -- never from generated data, and never from an HTTP request.
    ``replace`` re-runs the config's own validation, so an override is checked
    exactly like a configured value.
    """
    config = load_review_persistence_config()
    if review_db is not None:
        config = replace(config, database_path=review_db)
    database = open_review_database(config)
    return database, config.database_path


def _print_registration(
    database_path: Path,
    queue: ReviewQueue,
    registration: ReviewWorkflowRegistration,
) -> None:
    """Counts, a path, and which queue was written. Never a record or an edge.

    An operator needs to know which queue was written and how much of it is
    still waiting for a reviewer; everything else in a workflow is customer
    data that has no business on a terminal.

    A workflow with no review cases prints the counts and nothing more. Neither
    branch below applies to it, and saying "already registered" or "created"
    would both be guesses -- the case counts cannot tell whether the stored
    authorization context is new.
    """
    print(f"Database: {database_path}")
    print(f"Review queue: {queue.name} ({queue.review_queue_id})")
    print(
        f"Cases: {registration.total_cases} total "
        f"({registration.pending_cases} pending, {registration.resolved_cases} resolved)."
    )
    if registration.created_anything:
        print(f"Registered {registration.newly_registered} new review case(s).")
    elif registration.was_idempotent:
        print("This workflow was already registered; no case was written.")


def _missing_registration_targets(args: argparse.Namespace) -> list[str]:
    """Which registration targets the operator left unnamed, if any.

    Both are reported together rather than one at a time, so an operator who
    omitted both is not sent round the loop twice. Returns an empty list when
    registration was not requested at all.
    """
    if not args.register_review_queue:
        return []
    return [
        flag
        for flag, value in (
            ("--organization <slug>", args.organization),
            ("--review-queue <name>", args.review_queue),
        )
        if not value
    ]


def _require_organization(tenants: SqliteTenantRepository, slug: str) -> Organization:
    """Resolve a slug to an existing organization, or refuse.

    Never creates one. A command that manufactured a tenant from an unmatched
    slug would turn a typo into a new customer, and every review case
    registered afterwards would belong to it.
    """
    organization = tenants.get_organization_by_slug(slug)
    if organization is None:
        raise IdentityNotFoundError(
            f"No organization has the slug {slug!r}. Create it first with "
            "'create-organization'; this command will not create one for you."
        )
    return organization


def _require_review_queue(
    tenants: SqliteTenantRepository,
    *,
    organization: Organization,
    name: str,
) -> ReviewQueue:
    """Resolve an existing queue inside this organization, or refuse.

    Never creates one. Registering a workflow and provisioning a queue are two
    different operator decisions, and a command that did both would turn a
    misspelled queue name into a new, empty, permanently orphaned queue --
    silently, and inside a real tenant.

    The lookup is scoped to the organization, so a queue of that name owned by
    a *different* organization is simply absent here. The operator is told the
    queue does not exist in the organization they named, and learns nothing
    about who else might own one.
    """
    queue = tenants.get_review_queue_by_name(
        organization_id=organization.organization_id, name=name
    )
    if queue is None:
        raise IdentityNotFoundError(
            f"Review queue {name!r} does not exist in organization "
            f"{organization.slug!r}. Create it explicitly with 'create-review-queue' "
            "before registering a workflow; this command will not create one for you."
        )
    return queue


def _register_queue(
    state: ReviewWorkflowState,
    *,
    records: Sequence[EntityRecord],
    resolution: ResolutionResult,
    entity_resolution_config_path: Path,
    review_db: Path | None,
    organization_slug: str,
    queue_name: str,
) -> int:
    """Persist the generated workflow into one existing review queue.

    Both the organization and the queue are resolved before anything is
    written, and neither is created. This command generates a workflow; it does
    not provision tenant resources.

    The config path is stored with the context so a later resolution authorizes
    against the thresholds the queue was generated with, rather than whatever
    the default config happens to say by then.

    The repository is bound to the resolved queue, so every row this writes --
    context, cases, and their future history -- is owned by that queue and,
    through it, by exactly one organization.
    """
    database, database_path = _open_review_queue(review_db)
    try:
        tenants = SqliteTenantRepository(database)
        organization = _require_organization(tenants, organization_slug)
        queue = _require_review_queue(tenants, organization=organization, name=queue_name)
        registration = register_review_workflow(
            SqliteReviewCaseRepository(database, review_queue_id=queue.review_queue_id),
            state=state,
            entity_records=records,
            resolution=resolution,
            entity_resolution_config_path=str(entity_resolution_config_path),
        )
    finally:
        database.close()
    print(f"Organization: {organization.slug} ({organization.organization_id})")
    _print_registration(database_path, queue, registration)
    return EXIT_OK


def _create_organization(args: argparse.Namespace) -> int:
    database, database_path = _open_review_queue(args.review_db)
    try:
        tenants = SqliteTenantRepository(database)
        organization = tenants.create_organization(
            Organization.create(
                slug=args.slug,
                display_name=args.name,
                created_at_utc=tenants.timestamp(),
            )
        )
    finally:
        database.close()
    print(f"Database: {database_path}")
    print(f"Created organization {organization.slug} ({organization.organization_id}).")
    return EXIT_OK


def _list_organizations(args: argparse.Namespace) -> int:
    """Slugs and ids only. An operator needs to know what to pass, nothing else."""
    database, database_path = _open_review_queue(args.review_db)
    try:
        organizations = SqliteTenantRepository(database).list_organizations()
    finally:
        database.close()
    print(f"Database: {database_path}")
    if not organizations:
        print("No organizations are registered.")
        return EXIT_OK
    for organization in organizations:
        print(f"{organization.slug}\t{organization.organization_id}\t{organization.status.value}")
    return EXIT_OK


def _prompt_for_password() -> str | None:
    """Read a password twice without echoing it, or return None on mismatch.

    ``getpass`` rather than ``input``: it keeps the characters off the screen
    and out of the terminal's scrollback, which is where a shoulder-surfer and
    a screen recording both look.

    Confirmation is required because this is the only chance to get it right --
    there is no reset flow, so a typo would produce an account nobody can sign
    into and no one could diagnose. On mismatch nothing is written; the
    operator runs the command again.

    Neither value is echoed, logged, or placed in an error message on any path.
    """
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        return None
    return password


def _create_user(args: argparse.Namespace) -> int:
    """Create a user and their first password, atomically.

    The password is read before the database is opened, so an operator who
    mistypes the confirmation never touches storage at all.
    """
    password = _prompt_for_password()
    if password is None:
        print("The passwords did not match. No user was created.")
        return EXIT_USAGE

    database, database_path = _open_review_queue(args.review_db)
    try:
        service = UserProvisioningService(
            provisioning=SqliteUserProvisioningRepository(database),
            hasher=Argon2idPasswordHasher(),
        )
        user = service.provision_user(
            email=args.email,
            display_name=args.display_name,
            password=password,
        )
    finally:
        database.close()

    # The identifier and the address the operator typed. Never the password,
    # never the hash, and never anything derived from either.
    print(f"Database: {database_path}")
    print(f"Created user {user.email} ({user.user_id}).")
    print("This user has no organization membership yet, and can therefore reach no queue.")
    print("Grant one with: add-membership --user <email> --organization <slug> --role <ROLE>")
    return EXIT_OK


def _add_membership(args: argparse.Namespace) -> int:
    """Join one existing user to one existing organization in one explicit role.

    Creates neither side. A user who does not exist is refused rather than
    provisioned, because provisioning one here would mean an account with no
    password that nobody can sign into; an organization that does not exist is
    refused because a typo must not become a tenant.

    Duplicates are deterministic: a user holds exactly one membership per
    organization, enforced by the schema, so a second grant is refused with the
    tenant exit code and the stored role is left exactly as it was. Changing
    someone's role is therefore not something this command can do by accident.

    The role is required and has no default. A default would be a silent
    decision about whether someone may record human decisions on customer data.
    """
    database, database_path = _open_review_queue(args.review_db)
    try:
        tenants = SqliteTenantRepository(database)
        organization = _require_organization(tenants, args.organization)
        user = tenants.get_user_by_email(args.user)
        if user is None:
            # The address is not echoed back into a "no such user" message: this
            # command is operator-only today, and a message that quotes the
            # address is an account oracle the moment anything else can reach it.
            raise IdentityNotFoundError(
                "No user has that login address. Create one first with 'create-user'; "
                "this command will not create one for you."
            )
        membership = tenants.create_membership(
            OrganizationMembership.create(
                organization_id=organization.organization_id,
                user_id=user.user_id,
                role=MembershipRole(args.role),
                created_at_utc=tenants.timestamp(),
            )
        )
    finally:
        database.close()
    print(f"Database: {database_path}")
    print(
        f"Granted {membership.role.value} in organization {organization.slug} "
        f"to user {user.user_id}."
    )
    return EXIT_OK


def _create_review_queue(args: argparse.Namespace) -> int:
    database, database_path = _open_review_queue(args.review_db)
    try:
        tenants = SqliteTenantRepository(database)
        organization = _require_organization(tenants, args.organization)
        queue = tenants.create_review_queue(
            ReviewQueue.create(
                organization_id=organization.organization_id,
                name=args.name,
                created_at_utc=tenants.timestamp(),
            )
        )
    finally:
        database.close()
    print(f"Database: {database_path}")
    print(
        f"Created review queue {queue.name!r} ({queue.review_queue_id}) "
        f"in organization {organization.slug}."
    )
    return EXIT_OK


def _verify_queue(args: argparse.Namespace) -> int:
    """Report one queue's integrity and exit 0 only when it is clean.

    The exit code is the contract: 0 for a healthy queue,
    ``EXIT_INTEGRITY_FAILED`` for one with findings, and the existing tenant
    exit code for a name that does not resolve. That makes the command usable
    from a script without parsing its output.

    Every line printed is an identifier, a count, a code or a sentence this
    codebase wrote. No record value, reviewer label or context payload reaches
    the terminal -- an integrity report is something an operator pastes into an
    issue.
    """
    database, database_path = _open_review_queue(args.review_db)
    try:
        tenants = SqliteTenantRepository(database)
        organization = _require_organization(tenants, args.organization)
        queue = _require_review_queue(tenants, organization=organization, name=args.review_queue)
        report = verify_review_queue(database, review_queue_id=queue.review_queue_id)
    finally:
        database.close()

    print(f"Database: {database_path}")
    print(f"Organization: {organization.slug} ({organization.organization_id})")
    print(f"Review queue: {queue.name} ({queue.review_queue_id})")
    print(f"Checks run: {len(report.checks_run)}")
    for check in report.checks_run:
        print(f"  - {check}")

    if report.ok:
        print("Result: OK. No integrity findings.")
        return EXIT_OK

    print(f"Result: {len(report.findings)} integrity finding(s).")
    for finding in report.findings:
        print(f"  [{finding.code}] {finding.detail}")
    print("Nothing was modified. This command verifies; it does not repair.")
    return EXIT_INTEGRITY_FAILED


def _refuse_report_resolution() -> int:
    """Explain that report-based resolution is gone, and name what replaced it.

    The old behaviour resolved a case inside a loaded JSON report and wrote a
    new report. Nothing about that was wrong in isolation; what was wrong was
    that it created a *second* authoritative record of human decisions.

    Sprint 08 authorization is evaluated against one queue: a MATCH is refused
    when it would transitively contradict a recorded NO_MATCH anywhere in the
    same component. That check reads the durable queue. A NO_MATCH that existed
    only inside a JSON file was therefore invisible to it, so the
    transitive-safety guarantee was true of each store separately and false of
    the system.

    There is now one authoritative store, and one path into it:
    ``ReviewQueueService`` -> ``ReviewWorkflow`` -> Sprint 08 authorization ->
    SQLite. Reports remain an export -- ``list`` and ``inspect`` still read them
    -- and are no longer a place decisions are made.

    No CLI replacement is offered here rather than rerouted through the service,
    and that is deliberate. A resolution now carries a server-derived reviewer
    identity and passes tenant authorization: an operator command that resolved
    cases would need its own answer to which authenticated user is deciding and
    whether their membership permits it, which is a new privileged write path
    with its own security design. Phase A closes a second authority; it does not
    open a third.
    """
    print("'resolve' was removed in Sprint 14 Phase A. Nothing was resolved and no report")
    print("was written.")
    print()
    print("Resolving inside a JSON report made that report a second authoritative store of")
    print("human decisions. Sprint 08 MATCH authorization is evaluated against one durable")
    print("queue, so a NO_MATCH recorded only in a report did not constrain a later MATCH,")
    print("and the transitive-safety guarantee held for each store but not for the system.")
    print()
    print("Resolve through the authenticated review API, which is the one path that reaches")
    print("the authoritative queue:")
    print()
    print("  POST /api/v1/organizations/{organization_id}/review-queues/{review_queue_id}")
    print("       /review-cases/{review_case_id}/resolve")
    print()
    print("Start it with 'python -m review_api'. Reports are still an export: use 'list' and")
    print("'inspect' to read one.")
    return EXIT_USAGE


def _generate(args: argparse.Namespace) -> int:
    ingestion_config = load_ingestion_config(args.ingestion_config)
    resolution_config = load_entity_resolution_config(args.entity_resolution_config)
    records = _load_records(args.input_path, ingestion_config)
    resolution = resolve_entities(records, config=resolution_config)
    state = generate_review_cases(resolution, config=resolution_config)
    report_path = write_review_reports(
        ReviewWorkflow(state).to_outcome(),
        output_directory=args.report_dir,
        entity_records=records,
        resolution=resolution,
        entity_resolution_config_path=args.entity_resolution_config,
    )
    print(f"Generated {len(state.cases)} review cases.")
    print(f"Report: {report_path}")

    if not args.register_review_queue:
        return EXIT_OK
    return _register_queue(
        state,
        records=records,
        resolution=resolution,
        entity_resolution_config_path=args.entity_resolution_config,
        review_db=args.review_db,
        organization_slug=args.organization,
        queue_name=args.review_queue,
    )


def main() -> int:
    args = parse_args()
    try:
        if args.command == "create-organization":
            return _create_organization(args)
        if args.command == "list-organizations":
            return _list_organizations(args)
        if args.command == "create-user":
            return _create_user(args)
        if args.command == "add-membership":
            return _add_membership(args)
        if args.command == "create-review-queue":
            return _create_review_queue(args)
        if args.command == "verify-queue":
            return _verify_queue(args)

        if args.command == "generate":
            if args.review_db is not None and not args.register_review_queue:
                print("--review-db has no effect without --register-review-queue.")
                return EXIT_USAGE
            missing = _missing_registration_targets(args)
            if missing:
                # Refused up front rather than defaulted. Neither a tenant nor
                # a queue has a sensible default: review data owned by an
                # organization nobody named is review data nobody owns, and a
                # queue conjured from an omitted argument is a tenant resource
                # created by accident.
                print(
                    f"--register-review-queue requires {' and '.join(missing)}. "
                    "Both must already exist; create them with 'create-organization' "
                    "and 'create-review-queue' first."
                )
                return EXIT_USAGE
            return _generate(args)

        if args.command == "resolve":
            # Refused before the report is even read. The operator's problem is
            # not their arguments, so validating them further would only delay
            # the one thing they need to know.
            return _refuse_report_resolution()

        loaded = load_human_review_report(args.report_path)
        workflow = ReviewWorkflow(loaded.outcome.workflow_state)
        if args.command == "list":
            for case in workflow.list_cases():
                print(
                    f"{case.review_case_id}\t{case.status.value}\t"
                    f"{case.pair.record_a_id}\t{case.pair.record_b_id}"
                )
            return EXIT_OK

        if args.command == "inspect":
            case = workflow.get_case(args.review_case_id)
            print(json.dumps(case.to_dict(), indent=2, ensure_ascii=False))
            return EXIT_OK

        return EXIT_USAGE
    except IngestionError as exc:
        print(f"Ingestion error [{exc.code}]: {exc.message}")
        return EXIT_INGESTION
    except HumanReviewReportError as exc:
        print(f"Review report error: {exc}")
        return EXIT_REPORT
    except HumanReviewError as exc:
        print(f"Human review rejected: {exc}")
        return EXIT_POLICY
    except IdentityValidationError as exc:
        # A rejected password or a malformed address. The message names the
        # rule that was broken and never the value that broke it.
        print(f"Invalid input: {exc}")
        return EXIT_USAGE
    except IdentityError as exc:
        # A tenant problem, not a review problem: an unknown slug, a duplicate
        # organization, a queue name already taken. Nothing was written.
        print(f"Organization/tenant refused: {exc}")
        return EXIT_TENANT_REFUSED
    except ReviewApplicationError as exc:
        # Every one of these means the stored queue was left exactly as it was:
        # a changed record set, a changed AUTO_MATCH snapshot, a different
        # entity-resolution config, or storage itself refusing. The operator
        # gets the refusal, not a traceback.
        print(f"Review queue registration refused: {exc}")
        return EXIT_REGISTRATION_REFUSED
    except (OSError, ValueError, KeyError) as exc:
        print(f"Human review command failed: {exc}")
        return EXIT_REPORT


if __name__ == "__main__":
    raise SystemExit(main())
