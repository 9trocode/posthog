from temporalio import activity

from products.replay_vision.backend.billing import observation_credits_for_model
from products.replay_vision.backend.models.replay_scanner import ReplayScanner, initial_watermark
from products.replay_vision.backend.quota import compute_scanner_budget
from products.replay_vision.backend.temporal.decorators import track_activity
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
    """
    scanner = ReplayScanner.objects.filter(pk=inputs.scanner_id, team_id=inputs.team_id).select_related("team").first()
    if scanner is None:
        # The reconciler removes schedules for deleted scanners; a racing tick just stops here.
        return CheckScannerBudgetOutput(capped=False)
    if scanner.monthly_credit_limit is None:
        return CheckScannerBudgetOutput(capped=False)
    budget = compute_scanner_budget(scanner)
    if not budget.would_exceed(observation_credits_for_model(scanner.model)):
        return CheckScannerBudgetOutput(capped=False)
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
            "credit_limit": budget.credit_limit,
            "credits_used": budget.credits_used,
        },
    )
    return CheckScannerBudgetOutput(capped=True)
