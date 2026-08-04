"""Keeps `Team.session_recording_linked_flag` in step with the flag key it points at.

The stored dict carries both the flag `id` and its `key`, but the SDK payload that
`RemoteConfig._build_session_recording_config` builds is derived from the key alone. Both the
browser and React Native SDKs treat a linked flag they can't resolve as "do not record", so a
stale key silently turns replay off for the team rather than surfacing an error anywhere.
"""

from typing import Any

from django.db import transaction
from django.db.models import QuerySet

from posthog.models import Team
from posthog.models.signals import model_activity_signal, mutable_receiver

from products.feature_flags.backend.models.feature_flag import FeatureFlag


def teams_linking_flag(feature_flag: FeatureFlag) -> QuerySet[Team]:
    """Every team gating session recording on this flag."""
    # Scoped by project rather than by team: any team in the project can gate recording on a flag
    # owned by a sibling team.
    return Team.objects.filter(
        project_id=feature_flag.team.project_id,
        session_recording_linked_flag__contains={"id": feature_flag.id},
    )


def update_linked_flag_key(team: Team, new_key: str) -> None:
    """Rewrite the stored key on a team's replay link, leaving teams that already match alone."""
    linked_flag = team.session_recording_linked_flag
    if not isinstance(linked_flag, dict) or linked_flag.get("key") == new_key:
        # A no-op save would still spend a write, a Celery task, and a RemoteConfig rebuild.
        return

    team.session_recording_linked_flag = {**linked_flag, "key": new_key}
    # Saving the instance rather than issuing a queryset `update()` is what fires the `post_save`
    # receiver that refreshes the team's RemoteConfig; a bulk update would leave the cached SDK
    # payload holding the old key.
    team.save(update_fields=["session_recording_linked_flag"])


def relink_teams_after_key_change(feature_flag: FeatureFlag) -> None:
    """Point every team gating replay on this flag at its current key."""
    for team in teams_linking_flag(feature_flag):
        update_linked_flag_key(team, feature_flag.key)


@mutable_receiver(model_activity_signal, sender=FeatureFlag)
def relink_teams_on_key_change(
    sender: Any, before_update: FeatureFlag | None, after_update: FeatureFlag | None, **kwargs: Any
) -> None:
    # Wired to the model signal rather than to FeatureFlagSerializer so renames from the Django
    # admin, a shell, or a Celery task keep the link intact too. `before_update` is None on create;
    # `after_update` is None on delete.
    if before_update is None or after_update is None or before_update.key == after_update.key:
        return

    # Deferred to commit because the serializer renames inside a transaction that holds
    # `select_for_update` on the flag row, and taking team locks in that window invites deadlocks.
    # Outside a transaction (admin, shell) `on_commit` runs the callback immediately.
    transaction.on_commit(lambda: relink_teams_after_key_change(after_update))
