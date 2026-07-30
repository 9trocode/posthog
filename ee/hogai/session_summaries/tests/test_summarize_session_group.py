from datetime import UTC, datetime

import pytest
from unittest.mock import MagicMock, patch

from rest_framework import exceptions
from temporalio.exceptions import ApplicationError

from posthog.session_recordings.queries.session_replay_events import SessionReplayEvents, SessionsWithTimestamps

from ee.hogai.session_summaries.session_group.patterns import (
    RawSessionGroupSummaryPattern,
    RawSessionGroupSummaryPatternsList,
    combine_patterns_with_events_context,
)
from ee.hogai.session_summaries.session_group.summarize_session_group import find_sessions_timestamps_dropping_missing

MIN_TS = datetime(2026, 7, 29, 8, 0, 0, tzinfo=UTC)
MAX_TS = datetime(2026, 7, 29, 9, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "requested,found_in_db,expected_found,expected_missing",
    [
        # One recording dropped out between validation reads: keep the rest instead of failing the batch
        (["s-1", "s-2", "s-3"], {"s-1", "s-3"}, ["s-1", "s-3"], ["s-2"]),
        (["s-1", "s-2"], {"s-1", "s-2"}, ["s-1", "s-2"], []),
        # Duplicate requested IDs must not spawn duplicate summarization tasks: dedupe found and missing in order
        (["s-1", "s-1", "s-2", "s-2", "s-3"], {"s-1"}, ["s-1"], ["s-2", "s-3"]),
    ],
)
def test_find_sessions_timestamps_dropping_missing(
    requested: list[str],
    found_in_db: set[str],
    expected_found: list[str],
    expected_missing: list[str],
) -> None:
    query_result = SessionsWithTimestamps(session_ids=found_in_db, min_timestamp=MIN_TS, max_timestamp=MAX_TS)
    with patch.object(SessionReplayEvents, "sessions_found_with_timestamps", return_value=query_result):
        found, missing, min_timestamp, max_timestamp = find_sessions_timestamps_dropping_missing(
            session_ids=requested, team=MagicMock(id=1)
        )
    assert found == expected_found
    assert missing == expected_missing
    assert min_timestamp == MIN_TS
    assert max_timestamp == MAX_TS


def test_find_sessions_timestamps_dropping_missing_raises_when_no_sessions_found() -> None:
    query_result = SessionsWithTimestamps(session_ids=set(), min_timestamp=None, max_timestamp=None)
    with patch.object(SessionReplayEvents, "sessions_found_with_timestamps", return_value=query_result):
        with pytest.raises(exceptions.ValidationError, match="Session recordings not found"):
            find_sessions_timestamps_dropping_missing(session_ids=["s-1", "s-2"], team=MagicMock(id=1))


def test_combine_patterns_with_events_context_stays_retryable_when_all_patterns_fail() -> None:
    patterns = RawSessionGroupSummaryPatternsList(
        patterns=[
            RawSessionGroupSummaryPattern(
                pattern_id=1,
                pattern_name="p",
                pattern_description="d",
                severity="low",
                indicators=["i"],
            )
        ]
    )
    with pytest.raises(ApplicationError) as exc_info:
        combine_patterns_with_events_context(
            patterns=patterns,
            pattern_id_to_event_context_mapping={},
            session_ids=["s-1"],
            user_id=1,
        )
    assert exc_info.value.non_retryable is False
