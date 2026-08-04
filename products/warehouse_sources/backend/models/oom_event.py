from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import models
from django.db.models import Count
from django.utils import timezone

from dateutil import parser

from posthog.models.scoping.root_mixin import TeamScopedRootMixin
from posthog.models.utils import UUIDModel, sane_repr

if TYPE_CHECKING:
    from products.warehouse_sources.backend.models.external_data_schema import ExternalDataSchema

# Self-reported phases that rule a death out as a *merge* memory problem. Repartitioning only changes
# merge memory, so a death in any of these phases must not feed the repartition trigger, whatever
# killed the worker (an extract-phase OOM is real, but its remedy is chunking or routing, not a finer
# partition layout). An unknown phase (no report: rollout gap, expired key, Redis down) fails open.
NON_MERGE_PHASES = ("extract", "load", "finished")


def infra_burst_window_seconds() -> int:
    return int(getattr(settings, "DATA_WAREHOUSE_OOM_INFRA_BURST_WINDOW_SECONDS", 1800))


def infra_burst_min_schemas() -> int:
    return int(getattr(settings, "DATA_WAREHOUSE_OOM_INFRA_BURST_MIN_SCHEMAS", 50))


def infra_burst_min_teams() -> int:
    return int(getattr(settings, "DATA_WAREHOUSE_OOM_INFRA_BURST_MIN_TEAMS", 10))


def evidence_freshness_bound_seconds() -> float:
    # A phase flip reaches Redis only on the next periodic sample, so evidence up to one interval old
    # is normal. Twice the interval tolerates one missed write; beyond that the report describes what
    # the run was doing earlier, not what killed it.
    return 2.0 * float(getattr(settings, "DATA_WAREHOUSE_WORKLOAD_REPORT_INTERVAL_SECONDS", 30.0))


class ExternalDataSchemaOOMEvent(TeamScopedRootMixin, UUIDModel):
    """Append-only log of *suspected* sync OOMs for an external data schema.

    Suspected, not confirmed, and the distinction is the point. What is actually detected is that the
    previous attempt stopped heartbeating, which is equally what a deploy, a pod eviction, a node
    drain, a native crash and a heartbeat lost by a healthy worker look like. Nothing in the signal
    itself distinguishes those from a real out-of-memory kill.

    Each row therefore snapshots the workload self-report evidence available at recording time (see
    `workload_report.py`): what phase the dead attempt was in, how big its own in-memory buffer was,
    and *aggregates only* about its pod co-tenants. Co-tenant identifiers are never stored — a pod is
    multi-tenant, so they belong to other teams, and this is a team-scoped row. Evidence is stored
    raw rather than as a baked verdict so the rules in `recent_count` stay tunable after the fact.

    A row is written once per Temporal retry attempt that follows a silent death, so this is an
    occurrence log rather than a counter.
    """

    # db_constraint=False on the Team FK: a real constraint takes a SHARE ROW EXCLUSIVE lock on the
    # hot posthog_team table on create. Team scoping is enforced at the app layer by TeamScopedRootMixin.
    team = models.ForeignKey("posthog.Team", on_delete=models.CASCADE, db_constraint=False)
    schema = models.ForeignKey(
        "warehouse_sources.ExternalDataSchema", on_delete=models.CASCADE, related_name="oom_events"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    # Context captured from the prior attempt's last heartbeat.
    run_id = models.CharField(max_length=400, null=True, blank=True)
    host = models.CharField(max_length=400, null=True, blank=True)
    gap_seconds = models.FloatField(null=True, blank=True)
    # Workload self-report evidence, snapshotted at recording time because its Redis source expires.
    # Null means "no report available", which every rule treats as unknown, never as exonerating.
    self_phase = models.CharField(max_length=32, null=True, blank=True)
    # Seconds between the evidence being flushed and the run's last heartbeat (its best-known death
    # time). Stale evidence describes what the run was doing earlier, so the rules refuse to
    # exonerate on it (see `_evidence_excludes`).
    self_report_age_seconds = models.FloatField(null=True, blank=True)
    self_peak_buffer_bytes = models.BigIntegerField(null=True, blank=True)
    # Aggregates only — never co-tenant schema/run ids (they belong to other teams).
    co_tenant_max_peak_buffer_bytes = models.BigIntegerField(null=True, blank=True)
    co_tenant_report_count = models.IntegerField(null=True, blank=True)

    all_teams = models.Manager()  # noqa: DJ012 — both are managers, ruff misclassifies this

    __repr__ = sane_repr("schema_id", "created_at")

    class Meta:
        # Django framework internals (cascade delete, related-object access, prefetch) read through
        # `_default_manager` / `_base_manager` and expect an unfiltered manager. Point them at the plain
        # `all_teams` so a schema delete that cascades to the log doesn't hit the fail-closed manager.
        # `objects` (from TeamScopedRootMixin) stays fail-closed for explicit app code.
        default_manager_name = "all_teams"
        indexes = [
            models.Index(fields=["schema", "created_at"], name="dwh_oom_schema_created_idx"),
            # The burst rule counts distinct schemas fleet-wide around a timestamp, which can't ride
            # the schema-scoped index above. schema_id rides along so the COUNT(DISTINCT schema_id)
            # is an index-only scan.
            models.Index(fields=["created_at", "schema"], name="dwh_oom_created_idx"),
        ]

    @classmethod
    def recent_count(cls, schema: "ExternalDataSchema", *, days: int) -> int:
        """Occurrences within the last `days` that still look like this table's own merge OOM.

        Three rules narrow the raw stopped-heartbeating signal, each with a distinct evidence source,
        and each failing open (an occurrence the evidence cannot explain away stays counted):

        * **Phase** — a death self-reported in a non-merge phase is not a repartitioning problem,
          whatever killed the worker (see `NON_MERGE_PHASES`).
        * **Culprit** — a pod OOM kills every co-tenant. If something else on the pod self-reported a
          strictly larger peak buffer than us, we were plausibly collateral, not the cause. The
          snapshot only carries co-tenant peaks time-correlated with the death (see
          `enrich_death_event_properties`): a neighbour that crashed an hour earlier, or a lifetime
          peak long since released, must not exonerate a death it had nothing to do with.
        * **Infra burst** — a deploy, node drain or incident takes down many unrelated schemas at
          once; an occurrence sharing a window with a fleet-wide burst is attributed to infrastructure.
          A burst must span many distinct *teams* as well as schemas — one tenant's own outage is not
          infrastructure and must not suppress other tenants' counting.
          Judged at read time from the rows themselves, because the burst window extends past the
          occurrence being judged (later rows are part of its evidence).

        The phase and culprit rules additionally require the snapshot to be *fresh* relative to the
        death (see `_evidence_excludes`): self-reports are periodic, so stale evidence predates
        whatever killed the worker and cannot exonerate.

        Every retry attempt counts, including repeats within one job: each is a separate attempt at
        the same merge, so a job failing attempt after attempt is the clearest evidence the log carries.

        Every retry attempt that follows an OOM'd one counts, including repeats within a single job.
        Each is a separate attempt at the same merge, rescheduled onto whichever worker picks it up, so
        a job that OOMs on attempt after attempt is a table failing deterministically, which is the
        clearest evidence of a memory problem this log carries.

        `days` is required (no default) so it stays sourced from `DATA_WAREHOUSE_REPARTITION_OOM_WINDOW_DAYS`
        at the call site rather than duplicating that window here where the two could silently diverge.

        The window is also floored at the schema's `last_repartition_at`: a completed repartition
        addresses the OOMs that preceded it, so counting them again would re-trigger a repartition on
        the same (now healthy) table every cooldown until they age out.
        """
        since = timezone.now() - timedelta(days=days)
        last_repartition_at = schema.last_repartition_at
        if last_repartition_at:
            try:
                since = max(since, parser.parse(last_repartition_at))
            except (ValueError, TypeError):
                pass

        events = list(
            cls.objects.for_team(schema.team_id)
            .filter(schema_id=schema.pk, created_at__gte=since)
            .values(
                "created_at",
                "self_phase",
                "self_report_age_seconds",
                "self_peak_buffer_bytes",
                "co_tenant_max_peak_buffer_bytes",
            )
        )
        # Healthy schemas have no rows, so they pay for one indexed lookup and no rule evaluation.
        # The in-memory rules run first so exonerated rows never reach the burst check at all.
        candidates = [event for event in events if not cls._evidence_excludes(event)]
        if not candidates:
            return 0

        # Burst screening without a query per row: a sub-window's distinct-schema count can never
        # exceed the containing span's, so one aggregate over [min - window, max + window] proves a
        # quiet fleet burst-free for every candidate in it at once. Candidates days apart would
        # dilute that bound into uselessness (the span would cover the whole week and always trip),
        # so they are first clustered: timestamps closer than a window can share a burst, anything
        # farther apart cannot, and each cluster gets its own span aggregate. Only a cluster whose
        # own span crosses the threshold — the fleet was actually bursty around it — falls back to a
        # per-candidate exact window check, which stays an indexed COUNT, never a row fetch.
        window = timedelta(seconds=infra_burst_window_seconds())
        count = 0
        for cluster in cls._cluster_by_gap(sorted(event["created_at"] for event in candidates), window):
            if not cls._burst_thresholds_met(cluster[0] - window, cluster[-1] + window):
                count += len(cluster)
            else:
                count += sum(1 for created_at in cluster if not cls._is_infra_burst(created_at))
        return count

    @staticmethod
    def _cluster_by_gap(sorted_timestamps: list[Any], window: timedelta) -> list[list[Any]]:
        clusters: list[list[Any]] = []
        for timestamp in sorted_timestamps:
            if clusters and timestamp - clusters[-1][-1] <= window:
                clusters[-1].append(timestamp)
            else:
                clusters.append([timestamp])
        return clusters

    @classmethod
    def _evidence_excludes(cls, event: dict[str, Any]) -> bool:
        # Freshness gates every exoneration: a report flushed long before the death describes an
        # earlier phase of the run (a merge that OOMs within one report interval of leaving extract
        # still shows "extract" in Redis — the exact death this log exists to catch). Stale or
        # unknown-age evidence therefore cannot exclude; it fails open like missing evidence.
        age = event["self_report_age_seconds"]
        if age is None or age > evidence_freshness_bound_seconds():
            return False
        if event["self_phase"] in NON_MERGE_PHASES:
            return True
        own_peak = event["self_peak_buffer_bytes"]
        co_tenant_max = event["co_tenant_max_peak_buffer_bytes"]
        # Strictly larger, and only when both sides are known: a missing report on either side must
        # never exonerate, or a rollout gap would silently disable the trigger.
        return own_peak is not None and co_tenant_max is not None and co_tenant_max > own_peak

    @classmethod
    def _is_infra_burst(cls, created_at: Any) -> bool:
        window = timedelta(seconds=infra_burst_window_seconds())
        return cls._burst_thresholds_met(created_at - window, created_at + window)

    @classmethod
    def _burst_thresholds_met(cls, start: Any, end: Any) -> bool:
        # One aggregate query, two counts back, no rows transferred, however large the burst.
        # Deliberately cross-team (all_teams): the whole point is that a burst spans unrelated
        # tenants; only the counts are used, no team data crosses the boundary. Both counts are
        # monotone in the window (a sub-window's distinct count cannot exceed its containing
        # span's), which is what lets `recent_count` screen a whole cluster with one span query.
        #
        # Requiring distinct *teams* as well as schemas is what makes the verdict infrastructure:
        # one tenant's source going down takes out all of that tenant's schemas at once, and without
        # the team floor that single-tenant event would classify every other tenant's contemporaneous
        # death as infra — suppressing remediation the other tenants may genuinely need.
        stats = cls.all_teams.filter(created_at__gte=start, created_at__lte=end).aggregate(
            schemas=Count("schema_id", distinct=True), teams=Count("team_id", distinct=True)
        )
        return (stats["schemas"] or 0) >= infra_burst_min_schemas() and (stats["teams"] or 0) >= infra_burst_min_teams()
