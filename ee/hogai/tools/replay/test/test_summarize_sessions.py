from ee.hogai.session_summaries.session_group.patterns import EnrichedSessionGroupSummaryPatternsList
from ee.hogai.tools.replay.summarize_sessions import SummarizeSessionsTool


def test_stringify_group_summary_returns_explicit_message_for_empty_report() -> None:
    result = SummarizeSessionsTool._stringify_group_summary(EnrichedSessionGroupSummaryPatternsList(patterns=[]))
    assert result == "No recurring patterns or issues were found across the analyzed sessions."
