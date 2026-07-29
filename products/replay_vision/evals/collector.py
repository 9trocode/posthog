"""Implementation of the golden-dataset collector; run it via collect.py (needs Django configured).

Everything goes through a PostHog instance's public API with a personal API key, so it works
against any project the key can read: the replay-vision scanner/observation endpoints drive the
selection, the synced ``postgres.posthog_exportedasset`` warehouse table locates each session's
rasterized MP4 (system assets are invisible to the exports list endpoint), the exports content
endpoint downloads the bytes, and HogQL queries mirroring ``fetch_session_events`` rebuild each
session's ``ScannerLlmInputs``.
"""

import random
import shutil
import datetime as dt
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import requests
import structlog

from posthog.session_recordings.queries.session_replay_events import DEFAULT_EVENT_FIELDS

from products.replay_vision.backend.temporal.activities.ensure_session_asset import (
    _EXPORT_FORMAT,
    _MOUSE_TAIL,
    _PLAYBACK_SPEED,
    _RECORDING_FPS,
    _SHOW_METADATA_FOOTER,
)
from products.replay_vision.backend.temporal.activities.fetch_session_events import (
    _EVENTS_PER_PAGE,
    _EVENTS_TO_IGNORE,
    _EXTRA_FIELDS,
    _process_events,
)
from products.replay_vision.backend.temporal.types import EventTable, ScannerLlmInputs, ScannerSnapshot, SessionMetadata
from products.replay_vision.evals.dataset import GoldenCase, GoldenDataset, save_dataset

logger = structlog.get_logger(__name__)

# The exports table syncs into the warehouse with up to ~a day of lag, so freshly-created
# observations often have no findable asset yet; we over-fetch candidates and skip those.
_ASSET_LOOKUP_CHUNK = 100
_OBSERVATION_PAGE = 100


class PostHogApi:
    """Minimal authenticated client for the endpoints the collector needs."""

    def __init__(self, host: str, project_id: int, api_key: str) -> None:
        self.host = host.rstrip("/")
        self.project_id = project_id
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {api_key}"

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._session.get(f"{self.host}{path}", params=params, timeout=60)
        response.raise_for_status()
        return response.json()

    def paginate(
        self, path: str, params: dict[str, Any] | None = None, max_items: int | None = None
    ) -> Iterator[dict[str, Any]]:
        url: str | None = f"{self.host}{path}"
        yielded = 0
        while url:
            response = self._session.get(url, params=params, timeout=60)
            response.raise_for_status()
            data = response.json()
            for item in data.get("results", []):
                yield item
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return
            url = data.get("next")
            params = None

    def hogql(self, query: str, values: dict[str, Any] | None = None) -> list[list[Any]]:
        payload = {"query": {"kind": "HogQLQuery", "query": query, "values": values or {}}}
        response = self._session.post(
            f"{self.host}/api/environments/{self.project_id}/query/", json=payload, timeout=120
        )
        response.raise_for_status()
        return response.json().get("results") or []

    def download(self, path: str, target: Path) -> None:
        # The content endpoint 302s to a presigned S3 URL; requests drops the auth header on the
        # cross-host redirect, which is exactly right for a presigned link.
        with self._session.get(f"{self.host}{path}", timeout=300, stream=True) as response:
            response.raise_for_status()
            with target.open("wb") as handle:
                shutil.copyfileobj(response.raw, handle)


def order_candidates(candidates: list[dict[str, Any]], rng: random.Random) -> dict[str, list[dict[str, Any]]]:
    """Per scanner type: labeled observations first (they carry ground truth), then the rest uniformly shuffled."""
    by_type: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        by_type.setdefault(candidate["scanner_type"], []).append(candidate)
    ordered: dict[str, list[dict[str, Any]]] = {}
    for scanner_type, group in by_type.items():
        labeled = [c for c in group if c["observation"].get("label")]
        unlabeled = [c for c in group if not c["observation"].get("label")]
        rng.shuffle(unlabeled)
        ordered[scanner_type] = labeled + unlabeled
    return ordered


def _parse_ts(raw: Any) -> dt.datetime:
    # The query API returns project-timezone ISO strings; normalize to UTC to match the
    # UTC-native datetimes the production ClickHouse fetch produces.
    return dt.datetime.fromisoformat(str(raw)).astimezone(dt.UTC)


def lookup_video_assets(api: PostHogApi, session_ids: list[str]) -> dict[str, int]:
    """Map session id to the id of its system rasterized-MP4 asset, for sessions that still have one."""
    found: dict[str, int] = {}
    for start in range(0, len(session_ids), _ASSET_LOOKUP_CHUNK):
        chunk = session_ids[start : start + _ASSET_LOOKUP_CHUNK]
        rows = api.hogql(
            """
            SELECT JSONExtractString(toString(export_context), 'session_recording_id') AS sid, min(id) AS asset_id
            FROM postgres.posthog_exportedasset
            WHERE team_id = {team_id}
              AND export_format = {export_format}
              AND is_system
              AND expires_after > now()
              AND JSONExtractFloat(toString(export_context), 'playback_speed') = {playback_speed}
              AND JSONExtractInt(toString(export_context), 'recording_fps') = {recording_fps}
              AND JSONExtractBool(toString(export_context), 'show_metadata_footer') = {show_metadata_footer}
              AND JSONExtractBool(toString(export_context), 'mouse_tail') = {mouse_tail}
              AND JSONExtractString(toString(export_context), 'session_recording_id') IN {session_ids}
            GROUP BY sid
            """,
            {
                "team_id": api.project_id,
                "export_format": _EXPORT_FORMAT,
                "playback_speed": _PLAYBACK_SPEED,
                "recording_fps": _RECORDING_FPS,
                "show_metadata_footer": _SHOW_METADATA_FOOTER,
                "mouse_tail": _MOUSE_TAIL,
                "session_ids": chunk,
            },
        )
        for sid, asset_id in rows:
            found[str(sid)] = int(asset_id)
    return found


_METADATA_QUERY = """
SELECT distinct_id, start_time, end_time, click_count, keypress_count, mouse_activity_count,
       active_milliseconds, console_error_count, first_url
FROM session_replay_events
WHERE session_id = {session_id}
"""


def _fetch_events(
    api: PostHogApi, session_id: str, start: dt.datetime, end: dt.datetime
) -> tuple[list[str], list[list[Any]]]:
    fields = [*DEFAULT_EVENT_FIELDS, *_EXTRA_FIELDS]
    query = (
        f"SELECT {', '.join(fields)} FROM events"
        " WHERE timestamp >= {start_time} AND timestamp <= {end_time} AND $session_id = {session_id}"
        " AND event NOT IN {events_to_ignore}"
        " ORDER BY timestamp ASC LIMIT {limit} OFFSET {offset}"
    )
    columns: list[str] = []
    rows: list[list[Any]] = []
    offset = 0
    while True:
        page = api.hogql(
            query,
            {
                # Same 100s wiggle as production get_events_query: the range only bounds the scan.
                "start_time": (start - dt.timedelta(seconds=100)).isoformat(),
                "end_time": (end + dt.timedelta(seconds=100)).isoformat(),
                "session_id": session_id,
                "events_to_ignore": _EVENTS_TO_IGNORE,
                "limit": _EVENTS_PER_PAGE,
                "offset": offset,
            },
        )
        if not columns:
            columns = fields.copy()
        rows.extend(list(row) for row in page)
        if len(page) < _EVENTS_PER_PAGE:
            break
        offset += _EVENTS_PER_PAGE
    return columns, rows


def build_llm_inputs(api: PostHogApi, team_id: int, session_id: str) -> ScannerLlmInputs | None:
    """Rebuild the ScannerLlmInputs production stashed in Redis, from the query API instead of ClickHouse."""
    metadata_rows = api.hogql(_METADATA_QUERY, {"session_id": session_id})
    if not metadata_rows:
        return None
    (distinct_id, start_raw, end_raw, clicks, keypresses, mouse, active_ms, console_errors, first_url) = metadata_rows[
        0
    ]
    start, end = _parse_ts(start_raw), _parse_ts(end_raw)
    duration_seconds = (end - start).total_seconds()

    raw_columns, raw_rows = _fetch_events(api, session_id, start, end)
    if not raw_rows:
        return None
    # _process_events needs real datetimes to compute per-event offsets from session start.
    timestamp_index = raw_columns.index("timestamp")
    for row in raw_rows:
        row[timestamp_index] = _parse_ts(row[timestamp_index])
    # HogQL surfaces `properties.$window_id` as a bare `$window_id` column, matching production's runner output.
    columns = [column.removeprefix("properties.") for column in raw_columns]
    processed = _process_events(columns, raw_rows, session_start=start)

    active_seconds = float(active_ms or 0) / 1000
    return ScannerLlmInputs(
        session_id=session_id,
        team_id=team_id,
        events=EventTable(columns=processed.columns, rows=processed.rows),
        url_mapping=processed.url_mapping,
        window_mapping=processed.window_mapping,
        event_timestamps=processed.event_timestamps,
        navigation=processed.navigation,
        navigation_dropped=processed.navigation_dropped,
        distinct_id=str(distinct_id) if distinct_id else None,
        metadata=SessionMetadata(
            start_time=start,
            end_time=end,
            duration_seconds=duration_seconds,
            active_seconds=active_seconds,
            inactive_seconds=max(0.0, duration_seconds - active_seconds),
            click_count=clicks,
            keypress_count=keypresses,
            mouse_activity_count=mouse,
            start_url=first_url or None,
            console_error_count=console_errors,
        ),
    )


def _fetch_candidates(
    api: PostHogApi, scanner_ids: list[str] | None, max_observations_per_scanner: int
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for scanner in api.paginate(f"/api/projects/{api.project_id}/vision/scanners/", {"limit": 100}):
        if scanner_ids and scanner["id"] not in scanner_ids:
            continue
        observations = api.paginate(
            f"/api/projects/{api.project_id}/vision/scanners/{scanner['id']}/observations/",
            {"status": "succeeded", "limit": _OBSERVATION_PAGE},
            max_items=max_observations_per_scanner,
        )
        for observation in observations:
            if not (observation.get("scanner_result") or {}).get("model_output"):
                continue
            candidates.append({"observation": observation, "scanner": scanner, "scanner_type": scanner["scanner_type"]})
    return candidates


def _write_case(
    api: PostHogApi, root: Path, candidate: dict[str, Any], asset_id: int, team_name: str
) -> GoldenCase | None:
    observation = candidate["observation"]
    scanner = candidate["scanner"]
    label = observation.get("label") or {}
    case = GoldenCase(
        case_id=observation["id"],
        scanner_id=scanner["id"],
        scanner_name=scanner["name"],
        scanner_type=scanner["scanner_type"],
        session_id=observation["session_id"],
        team_id=api.project_id,
        team_name=team_name,
        snapshot=ScannerSnapshot.model_validate(observation["scanner_snapshot"]),
        recorded_output=observation["scanner_result"]["model_output"],
        label_is_correct=label.get("is_correct"),
        label_feedback=label.get("feedback") or "",
        collected_at=dt.datetime.now(dt.UTC).isoformat(),
    )
    case_dir = case.case_dir(root)
    if case.video_path(root).exists() and case.inputs_path(root).exists():
        return case

    inputs = build_llm_inputs(api, api.project_id, case.session_id)
    if inputs is None:
        logger.warning("collector.no_events_for_session", session_id=case.session_id)
        return None
    case_dir.mkdir(parents=True, exist_ok=True)
    case.inputs_path(root).write_text(inputs.model_dump_json())
    api.download(f"/api/environments/{api.project_id}/exports/{asset_id}/content/?download=true", case.video_path(root))
    return case


def collect(
    *,
    host: str,
    project_id: int,
    api_key: str,
    output: Path,
    per_type: int,
    scanner_ids: list[str] | None = None,
    seed: int = 42,
    max_observations_per_scanner: int = 200,
) -> GoldenDataset:
    api = PostHogApi(host, project_id, api_key)
    team_name = str(api.get_json(f"/api/projects/{project_id}/").get("name", ""))
    rng = random.Random(seed)

    candidates = _fetch_candidates(api, scanner_ids, max_observations_per_scanner)
    ordered = order_candidates(candidates, rng)
    all_session_ids = sorted({c["observation"]["session_id"] for c in candidates})
    assets = lookup_video_assets(api, all_session_ids)
    logger.info(
        "collector.candidates",
        candidates=len(candidates),
        with_video_asset=sum(1 for c in candidates if c["observation"]["session_id"] in assets),
        types={t: len(group) for t, group in ordered.items()},
    )

    cases: list[GoldenCase] = []
    for scanner_type, group in sorted(ordered.items()):
        collected = 0
        for candidate in group:
            if collected >= per_type:
                break
            session_id = candidate["observation"]["session_id"]
            if session_id not in assets:
                continue
            try:
                case = _write_case(api, output, candidate, assets[session_id], team_name)
            except requests.HTTPError as exc:
                logger.warning("collector.case_failed", observation_id=candidate["observation"]["id"], error=str(exc))
                continue
            if case is None:
                continue
            cases.append(case)
            collected += 1
        logger.info("collector.type_done", scanner_type=scanner_type, collected=collected, target=per_type)

    dataset = GoldenDataset(
        created_at=dt.datetime.now(dt.UTC).isoformat(), host=host, project_id=project_id, cases=cases
    )
    save_dataset(output, dataset)
    return dataset
