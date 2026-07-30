from temporalio import activity

from products.replay_vision.backend.billing import observation_credits_for_model
from products.replay_vision.backend.models.replay_scanner import ReplayScanner, initial_watermark
from products.replay_vision.backend.quota import CreditBudget, compute_scanner_budgets, current_period_bounds
from products.replay_vision.backend.temporal.decorators import track_activity
from products.replay_vision.backend.temporal.metrics import record_sweep_outcome
from products.replay_vision.backend.temporal.sweep_types import CheckScannerBudgetInputs, CheckScannerBudgetOutput


def _notify_limit_reached(scanner: ReplayScanner) -> None:
    """Best-effort realtime notification; a failure here must never affect the sweep's pause decision."""
    try:
        from products.notifications.backend.facade.api import (  # noqa: PLC0415 — keeps the heavy dep off the import path
            NotificationData,
            NotificationType,
            Priority,
            TargetType,
            create_notification,
        )

        create_notification(
            NotificationData(
                team_id=scanner.team_id,
                # A budget cap is a usage event, not breakage: the scanner is doing exactly what the
                # user configured, so it must not surface as a pipeline failure.
                notification_type=NotificationType.USAGE_SPIKE,
                priority=Priority.NORMAL,
                title=f'"{scanner.name}" reached its monthly credit limit',
                body=(
                    "It stopped scanning until its billing period resets. "
                    "Sessions skipped while capped are not scanned later."
                ),
                target_type=TargetType.TEAM,
                target_id=str(scanner.team_id),
                source_url=f"/replay-vision/{scanner.id}",
            )
        )
    except Exception:
        activity.logger.warning(
            "Failed to send scanner credit limit notification",
            extra={"scanner_id": str(scanner.id), "team_id": scanner.team_id},
            exc_info=True,
        )


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
    # Only genuine settled exhaustion is notified: the in-flight-only branch above is a transient
    # spike that may clear itself within minutes as reservations release, and notifying there could
    # tell a user their scanner stopped when it's about to resume on its own.
    period_start = current_period_bounds(scanner.team.organization_id).start
    should_notify = scanner.limit_notified_period_start != period_start
    # `.update` bypasses the model's version-tracking save(), matching how the watermark is advanced.
    # Stamping the notification flag in the same call means a crash between here and the send below
    # can only skip a notification, never send one without recording that it happened.
    ReplayScanner.objects.filter(pk=scanner.pk).update(
        last_swept_at=initial_watermark(),
        last_seen_session_id="",
        limit_notified_period_start=period_start,
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
    # Sent after the update commits: an email or realtime push can't be un-sent, so it must never
    # fire ahead of (or inside a transaction with) the state that records it happened.
    if should_notify:
        _notify_limit_reached(scanner)
    return CheckScannerBudgetOutput(capped=True)
