#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
from identity.errors import IdentityError, IdentityNotFoundError  # noqa: E402
from identity.models import Organization  # noqa: E402
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
from review_persistence.sqlite import (  # noqa: E402
    ReviewDatabase,
    SqliteReviewCaseRepository,
    SqliteTenantRepository,
    open_review_database,
)

# The queue name a registration uses when the operator does not choose one. A
# queue name is scoped to its organization, so this is a label inside a tenant
# the operator named explicitly -- never a tenant of its own.
DEFAULT_REVIEW_QUEUE_NAME = "default"

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_INGESTION = 2
EXIT_REPORT = 3
EXIT_POLICY = 4
EXIT_REGISTRATION_REFUSED = 5
EXIT_TENANT_REFUSED = 6


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
            "5 review queue registration refused, 6 organization/tenant refused."
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

    create_queue_parser = subparsers.add_parser(
        "create-review-queue",
        help="Create a review queue inside an existing organization.",
    )
    create_queue_parser.add_argument("--organization", type=str, required=True)
    create_queue_parser.add_argument("--name", type=str, required=True)
    _add_review_db_argument(create_queue_parser)

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
    # Which tenant owns the registered workflow is never inferred. The
    # organization must already exist; a misspelled slug is an error, not a new
    # customer. The queue inside it is named explicitly and created on first
    # use, which is safe in a way an implicit organization would not be: a
    # queue belongs to a tenant the operator has already named.
    generate_parser.add_argument(
        "--organization",
        type=str,
        default=None,
        help="Slug of the organization that owns the queue. Required with --register-review-queue.",
    )
    generate_parser.add_argument(
        "--review-queue",
        type=str,
        default=DEFAULT_REVIEW_QUEUE_NAME,
        help=(
            "Name of the review queue within the organization. Created if absent. "
            f"Default: {DEFAULT_REVIEW_QUEUE_NAME!r}."
        ),
    )
    _add_review_db_argument(generate_parser)

    list_parser = subparsers.add_parser("list", help="List review cases from a saved report.")
    list_parser.add_argument("report_path", type=Path)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect one review case.")
    inspect_parser.add_argument("report_path", type=Path)
    inspect_parser.add_argument("review_case_id", type=str)

    resolve_parser = subparsers.add_parser("resolve", help="Apply a human review decision.")
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


def _resolve_or_create_queue(
    tenants: SqliteTenantRepository,
    *,
    organization: Organization,
    name: str,
) -> tuple[ReviewQueue, bool]:
    """Find the named queue in this organization, creating it on first use.

    Returns the queue and whether it was created, so the operator is told which
    happened rather than having to infer it from the case counts.

    Creating here is safe in a way creating an organization would not be: the
    tenant was named explicitly on the command line and already exists, so the
    queue is a label inside a boundary the operator chose, not a new boundary.
    """
    existing = tenants.get_review_queue_by_name(
        organization_id=organization.organization_id, name=name
    )
    if existing is not None:
        return existing, False
    created = tenants.create_review_queue(
        ReviewQueue.create(
            organization_id=organization.organization_id,
            name=name,
            created_at_utc=tenants.timestamp(),
        )
    )
    return created, True


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
    """Persist the generated workflow into one organization's review queue.

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
        queue, created = _resolve_or_create_queue(
            tenants, organization=organization, name=queue_name
        )
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
    if created:
        print(f"Created review queue {queue.name!r}.")
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
        if args.command == "create-review-queue":
            return _create_review_queue(args)

        if args.command == "generate":
            if args.review_db is not None and not args.register_review_queue:
                print("--review-db has no effect without --register-review-queue.")
                return EXIT_USAGE
            if args.register_review_queue and not args.organization:
                # Refused up front rather than defaulted. There is no such thing
                # as a default tenant: review data owned by an organization
                # nobody named is review data nobody owns.
                print(
                    "--register-review-queue requires --organization <slug>. "
                    "Create one with 'create-organization' if it does not exist."
                )
                return EXIT_USAGE
            return _generate(args)

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

        if args.command == "resolve":
            config_path = (
                Path(loaded.entity_resolution_config_path)
                if loaded.entity_resolution_config_path
                else args.entity_resolution_config
            )
            resolution_config = load_entity_resolution_config(config_path)
            decision = HumanReviewDecision(args.decision)
            records_by_id = {record.record_id: record for record in loaded.entity_records}
            if not records_by_id:
                raise HumanReviewReportError(
                    "Persisted review report is missing entity records required for authorization."
                )
            if decision == HumanReviewDecision.MATCH:
                case = workflow.get_case(args.review_case_id)
                if (
                    case.pair.record_a_id not in records_by_id
                    or case.pair.record_b_id not in records_by_id
                ):
                    raise HumanReviewReportError(
                        "Persisted review report cannot reconstruct MATCH authorization "
                        "context for the reviewed records."
                    )
            workflow.resolve_case(
                args.review_case_id,
                decision=decision,
                reviewer_id=args.reviewer_id,
                resolution=loaded.resolution,
                records_by_id=records_by_id,
                entity_resolution_config=resolution_config,
            )
            report_path = write_review_reports(
                workflow.to_outcome(),
                output_directory=args.output_report_dir,
                entity_records=loaded.entity_records,
                resolution=loaded.resolution,
                entity_resolution_config_path=config_path,
            )
            print(f"Resolved {args.review_case_id} as {args.decision}.")
            print(f"Report: {report_path}")
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
