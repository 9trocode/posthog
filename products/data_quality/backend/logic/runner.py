"""Execute one check and record the result.

Never called in-request: every path into here comes from a Temporal activity, because a check runs
an arbitrary warehouse query and there is no response worth waiting for.

A check that cannot be compiled or executed is recorded as ``errored``, not raised. One broken
check must not take down the rest of its suite.
"""

import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from posthog.hogql.query import execute_hogql_query

from posthog.clickhouse.query_tagging import Feature, Product, tags_context
from posthog.models.scoping import team_scope
from posthog.models.team import Team
from posthog.models.user import User

from ..facade.enums import CheckRunStatus, CheckSeverity, SubjectStatus, SuiteRunTrigger
from ..models import DataQualityCheck, DataQualityCheckRun, DataQualitySuiteRun
from .compiler import compile_check, related_subject_ref
from .contracts import CompiledCheck, Evaluation
from .notifications import notify_check_started_failing
from .subject_access import check_type_reads_beyond_subject
from .subjects import resolve_subject

QUERY_TYPE = "data_quality_check"


@dataclass(frozen=True)
class CheckOutcome:
    """What one check execution produced, before it is written down."""

    status: CheckRunStatus
    failed_row_count: int | None = None
    observed_value: float | None = None
    compiled_query: str = ""
    error: str = ""
    became_failing: bool = False


def run_check(check: DataQualityCheck, suite_run: DataQualitySuiteRun, team: Team) -> CheckOutcome:
    """Compile, execute, and persist one check. Updates the check's denormalized status.

    Runs under an explicit team scope because there is no request context out here, and saving a
    check reads its own previous state through the fail-closed manager to build the activity log.
    """
    started_at = datetime.now(UTC)
    monotonic_start = time.monotonic()
    previous_status = check.last_status

    try:
        outcome = _execute(check, suite_run, team)
    except Exception as err:
        outcome = CheckOutcome(status=CheckRunStatus.ERRORED, error=str(err))

    duration_ms = int((time.monotonic() - monotonic_start) * 1000)
    with team_scope(team.id):
        _record_run(check, suite_run, outcome, started_at, duration_ms)
        _update_check(check, outcome)

    became_failing = (
        outcome.status is CheckRunStatus.FAILED
        and previous_status != CheckRunStatus.FAILED
        and check.severity == CheckSeverity.ERROR
    )
    if became_failing:
        notify_check_started_failing(check, outcome.failed_row_count)
    return replace(outcome, became_failing=became_failing)


@dataclass(frozen=True)
class _Authorization:
    """How one run's query executes against warehouse access control."""

    run_as: User | None
    bypass: bool


def _authorize(check: DataQualityCheck, suite_run: DataQualitySuiteRun) -> _Authorization | None:
    """How a run's query must execute so HogQL enforces the right warehouse access control.

    A check that reads beyond its declared subject -- ``custom_sql`` runs arbitrary HogQL,
    ``relationships`` also reads its target subject -- can otherwise be used to read a failing-row
    count over a table the caller was denied. The defense is to run the query as a user whose
    warehouse ACL HogQL will apply, so a denied object errors the check instead of leaking.

    - Manual runs execute as their initiator.
    - Scheduled and materialization runs have no initiator. A check constrained to only its declared
      subject exposes nothing the definition doesn't already name, so the service bypass is safe. A
      check that reads a further subject has no such guarantee, so it executes as the check's author;
      with no author there is nobody to authorize against, and returning ``None`` errors the run
      rather than bypassing the ACL over a subject the author may since have lost access to.
    """
    if suite_run.trigger == SuiteRunTrigger.MANUAL:
        return _Authorization(run_as=suite_run.created_by, bypass=suite_run.created_by is None)
    if not check_type_reads_beyond_subject(check.check_type):
        return _Authorization(run_as=None, bypass=True)
    if check.created_by is None:
        return None
    return _Authorization(run_as=check.created_by, bypass=False)


def _execute(check: DataQualityCheck, suite_run: DataQualitySuiteRun, team: Team) -> CheckOutcome:
    subject = resolve_subject(team.id, check.subject_type, check.subject_uuid)
    if not subject.exists:
        check.subject_status = SubjectStatus.ORPHANED
        return CheckOutcome(status=CheckRunStatus.SKIPPED, error="The subject no longer resolves.")

    check.subject_status = SubjectStatus.ACTIVE
    check.subject_name = subject.name

    authorization = _authorize(check, suite_run)
    if authorization is None:
        return CheckOutcome(
            status=CheckRunStatus.ERRORED,
            error="A scheduled check that reads another subject needs an author to authorize its warehouse access.",
        )

    related = related_subject_ref(check.check_type, check.config)
    compiled = compile_check(
        check_type=check.check_type,
        subject=subject,
        column_name=check.column_name,
        config=check.config,
        related_subject=resolve_subject(team.id, *related) if related else None,
    )
    with tags_context(product=Product.DATA_CATALOG, feature=Feature.ENRICHMENT):
        # The query runs as the user whose warehouse ACL HogQL should enforce (see _authorize). The
        # service-level bypass is only used where there is no actor and the query is constrained to
        # the check's declared subject, so it can't reach a warehouse object the definition doesn't
        # already name.
        response = execute_hogql_query(
            query=compiled.query,
            team=team,
            query_type=QUERY_TYPE,
            user=authorization.run_as,
            bypass_warehouse_access_control=authorization.bypass,
        )
    return _interpret(compiled, check.config, response.results, response.columns or [])


def _interpret(
    compiled: CompiledCheck,
    config: dict[str, Any],
    results: list[Any] | None,
    columns: list[str],
) -> CheckOutcome:
    if not results:
        return CheckOutcome(
            status=CheckRunStatus.ERRORED,
            compiled_query=compiled.printed_query,
            error="The check query returned no rows.",
        )

    row = dict(zip(columns, results[0]))
    observed = _as_float(row.get("observed_value"))

    if compiled.evaluation is Evaluation.BOUNDS:
        status = CheckRunStatus.PASSED if _within_bounds(observed, config) else CheckRunStatus.FAILED
        failed_row_count = None
    else:
        failed_row_count = int(row.get("failure_count") or 0)
        status = CheckRunStatus.PASSED if failed_row_count == 0 else CheckRunStatus.FAILED

    return CheckOutcome(
        status=status,
        failed_row_count=failed_row_count,
        observed_value=observed,
        compiled_query=compiled.printed_query,
    )


def _within_bounds(observed: float | None, config: dict[str, Any]) -> bool:
    if observed is None:
        return False
    minimum, maximum = config.get("min"), config.get("max")
    if minimum is not None and observed < minimum:
        return False
    return not (maximum is not None and observed > maximum)


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _record_run(
    check: DataQualityCheck,
    suite_run: DataQualitySuiteRun,
    outcome: CheckOutcome,
    started_at: datetime,
    duration_ms: int,
) -> None:
    DataQualityCheckRun.objects.for_team(check.team_id).create(
        team_id=check.team_id,
        quality_check=check,
        suite_run=suite_run,
        subject_type=check.subject_type,
        subject_uuid=check.subject_uuid,
        subject_name=check.subject_name,
        check_type=check.check_type,
        check_fingerprint=check.fingerprint,
        column_name=check.column_name,
        status=outcome.status,
        failed_row_count=outcome.failed_row_count,
        observed_value=outcome.observed_value,
        compiled_query=outcome.compiled_query,
        error=outcome.error,
        duration_ms=duration_ms,
        started_at=started_at,
        finished_at=datetime.now(UTC),
    )


def _update_check(check: DataQualityCheck, outcome: CheckOutcome) -> None:
    check.last_status = outcome.status
    check.last_run_at = datetime.now(UTC)
    check.save(update_fields=["last_status", "last_run_at", "subject_name", "subject_status", "updated_at"])
