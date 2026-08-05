from temporalio import activity

from products.replay_vision.backend.models.replay_scanner import ReplayScanner, initial_watermark
from products.replay_vision.backend.quota import compute_scanner_budget, current_period_bounds
from products.replay_vision.backend.temporal.decorators import track_activity
from products.replay_vision.backend.temporal.metrics import record_sweep_outcome
from products.replay_vision.backend.temporal.sweep_types import CheckScannerBudgetInputs, CheckScannerBudgetOutput


def _notify_limit_reached(scanner: ReplayScanner) -> bool:
    """Best-effort realtime notification; a failure here must never affect the sweep's pause decision.
    False means the send raised; a deliberate skip inside the pipeline (flag off, nobody to notify)
    still counts as sent, so it is not retried every tick."""
    try:
        from posthog.models import User  # noqa: PLC0415
        from posthog.rbac.user_access_control import UserAccessControl  # noqa: PLC0415

        from products.notifications.backend.facade.api import (  # noqa: PLC0415 — keeps the heavy dep off the import path
            NotificationData,
            NotificationType,
            Priority,
            RecipientsResolver,
            TargetType,
            create_notification,
        )

        class ScannerViewersResolver(RecipientsResolver):
            """Keeps only recipients with object-level viewer access: the pipeline's built-in
            access filter is resource-type wide, not per scanner."""

            def resolve(self, target_type: TargetType, target_id: str, team_id: int | None) -> list[int]:
                user_ids = super().resolve(target_type, target_id, team_id)
                users = list(User.objects.filter(id__in=user_ids))
                if not users or not UserAccessControl(users[0], scanner.team).access_controls_supported:
                    return user_ids
                return [
                    user.id
                    for user in users
                    if UserAccessControl(user, scanner.team).check_access_level_for_object(
                        scanner, required_level="viewer"
                    )
                ]

        create_notification(
            NotificationData(
                team_id=scanner.team_id,
                # A budget cap is a usage event, not breakage: the scanner is doing exactly what the
                # user configured, so it must not surface as a pipeline failure.
                notification_type=NotificationType.USAGE_SPIKE,
                priority=Priority.NORMAL,
                title=f'"{scanner.name}" reached its credit limit',
                body=(
                    "It stopped scanning until its billing period resets. "
                    "Sessions skipped while capped are not scanned later."
                ),
                target_type=TargetType.TEAM,
                target_id=str(scanner.team_id),
                resource_type="replay_scanner",
                resource_id=str(scanner.id),
                resolver=ScannerViewersResolver(),
                source_url=f"/replay-vision/{scanner.id}",
            )
        )
    except Exception:
        activity.logger.warning(
            "Failed to send scanner credit limit notification",
            extra={"scanner_id": str(scanner.id), "team_id": scanner.team_id},
            exc_info=True,
        )
        return False
    return True


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
        # The reconciler removes schedules for deleted scanners. A racing tick just stops here.
        return CheckScannerBudgetOutput(capped=False)
    if scanner.credit_limit is None:
        return CheckScannerBudgetOutput(capped=False)
    # One period resolution feeds both the cap decision and the notification stamp, so a tick that
    # spans a rollover or a billing sync cannot bind them to different periods.
    period = current_period_bounds(scanner.team.organization_id)
    budget = compute_scanner_budget(scanner, period)
    if not budget.blocked:
        return CheckScannerBudgetOutput(capped=False)
    if not budget.blocked_by_settled_spend:
        # Only the in-flight portion pushes this over: capped for now, but don't advance the
        # watermark, since those reservations may release without ever settling.
        record_sweep_outcome("scanner_capped_in_flight")
        activity.logger.info(
            "Sweep skipped: scanner credit limit reached by in-flight reservations only",
            extra={
                "scanner_id": str(inputs.scanner_id),
                "team_id": inputs.team_id,
                "credit_limit": budget.credit_limit,
                "credits_used": budget.credits_used,
            },
        )
        return CheckScannerBudgetOutput(capped=True)
    record_sweep_outcome("scanner_capped_settled")
    # Only genuine settled exhaustion is notified: the in-flight-only branch above is a transient
    # spike that may clear itself within minutes as reservations release, and notifying there could
    # tell a user their scanner stopped when it's about to resume on its own.
    # `.update` bypasses the model's version-tracking save(), matching how the watermark is advanced.
    # The stamp is claimed in the same UPDATE's WHERE, so of two racing ticks (a timed-out zombie
    # attempt overlapping the next) exactly one wins the send, and a crash between here and the send
    # below can only skip a notification, never send one without recording that it happened.
    stamped = (
        ReplayScanner.objects.filter(pk=scanner.pk)
        .exclude(limit_notified_period_start=period.start)
        .update(
            last_swept_at=initial_watermark(),
            last_seen_session_id="",
            limit_notified_period_start=period.start,
        )
    )
    if not stamped:
        # Already notified this period; still advance the watermark past the skipped window.
        ReplayScanner.objects.filter(pk=scanner.pk).update(last_swept_at=initial_watermark(), last_seen_session_id="")
    activity.logger.info(
        "Sweep skipped: scanner credit limit reached",
        extra={
            "scanner_id": str(inputs.scanner_id),
            "team_id": inputs.team_id,
            "credit_limit": budget.credit_limit,
            "credits_used": budget.credits_used,
        },
    )
    # Sent after the update commits: an email or realtime push can't be un-sent, so it must never
    # fire ahead of (or inside a transaction with) the state that records it happened.
    if stamped and not _notify_limit_reached(scanner):
        # The send raised before anything went out; hand the claim back so a transient outage
        # delays the period's one notification instead of consuming it.
        ReplayScanner.objects.filter(pk=scanner.pk, limit_notified_period_start=period.start).update(
            limit_notified_period_start=None
        )
    return CheckScannerBudgetOutput(capped=True)
