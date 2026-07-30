from temporalio import activity

from products.replay_vision.backend.billing import observation_credits_for_model
from products.replay_vision.backend.models.replay_scanner import ReplayScanner, initial_watermark
from products.replay_vision.backend.quota import CreditBudget, compute_scanner_budgets
from products.replay_vision.backend.temporal.decorators import track_activity
from products.replay_vision.backend.temporal.metrics import record_sweep_outcome
from products.replay_vision.backend.temporal.sweep_types import CheckScannerBudgetInputs, CheckScannerBudgetOutput


@activity.defn
@track_activity()
def check_scanner_budget_activity(inputs: CheckScannerBudgetInputs) -> CheckScannerBudgetOutput:
    """Whether this scanner has room for another observation under its own credit limit.

    On a cap, advances the watermark past the window the scanner is skipping. Freezing it instead would
    make the first uncapped tick fetch a candidate window stretching back to when the limit was hit and
    burn the fresh period's budget on stale recordings, so a spend limit would become a spend spike.
    Mirrors what re-enabling a disabled scanner already does. The reset lives here, not in the workflow,
    because it needs the real clock.

    The watermark only advances when settled credits alone exceed the limit. In-flight reservations are
    transient (a failed observation releases its reservation without ever writing a receipt), so a
    transient in-flight spike must not permanently skip a window the scanner could actually afford once
    those reservations clear.
    """
    scanner = ReplayScanner.objects.filter(pk=inputs.scanner_id, team_id=inputs.team_id).select_related("team").first()
    if scanner is None:
        # The reconciler removes schedules for deleted scanners; a racing tick just stops here.
        return CheckScannerBudgetOutput(capped=False)
    if scanner.monthly_credit_limit is None:
        return CheckScannerBudgetOutput(capped=False)
    spend = compute_scanner_budgets(scanner.team.organization_id, [scanner.id])[scanner.id]
    cost = observation_credits_for_model(scanner.model)
    if not spend.budget.would_exceed(cost):
        return CheckScannerBudgetOutput(capped=False)
    record_sweep_outcome("scanner_capped")
    settled_only = CreditBudget(credit_limit=spend.budget.credit_limit, credits_used=spend.credits)
    if not settled_only.would_exceed(cost):
        # Only the in-flight portion pushes this over: capped for now, but don't advance the
        # watermark, since those reservations may release without ever settling.
        activity.logger.info(
            "Sweep skipped: scanner credit limit reached by in-flight reservations only",
            extra={
                "scanner_id": str(inputs.scanner_id),
                "team_id": inputs.team_id,
                "credit_limit": spend.budget.credit_limit,
                "credits_used": spend.budget.credits_used,
            },
        )
        return CheckScannerBudgetOutput(capped=True)
    # `.update` bypasses the model's version-tracking save(), matching how the watermark is advanced.
    ReplayScanner.objects.filter(pk=scanner.pk).update(
        last_swept_at=initial_watermark(),
        last_seen_session_id="",
    )
    activity.logger.info(
        "Sweep skipped: scanner credit limit reached",
        extra={
            "scanner_id": str(inputs.scanner_id),
            "team_id": inputs.team_id,
            "credit_limit": spend.budget.credit_limit,
            "credits_used": spend.budget.credits_used,
        },
    )
    return CheckScannerBudgetOutput(capped=True)
