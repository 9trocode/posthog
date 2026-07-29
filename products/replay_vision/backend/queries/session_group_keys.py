import datetime as dt

import structlog

from posthog.hogql import ast
from posthog.hogql.parser import parse_select
from posthog.hogql.query import execute_hogql_query

from posthog.clickhouse.query_tagging import Feature, Product, tag_queries
from posthog.models import Team

logger = structlog.get_logger(__name__)

# The five `$group_N` columns every event carries; the team's own group types occupy a prefix of these.
GROUP_TYPE_INDEXES = range(5)

# Widens the timestamp window around the session so an event stamped slightly outside the recording's
# bounds (client clock skew) still contributes its group keys.
_TIMESTAMP_SLACK = dt.timedelta(hours=1)


def fetch_session_group_keys(*, team: Team, session_id: str, start: dt.datetime, end: dt.datetime) -> dict[int, str]:
    """Group keys the session's events carry, keyed by group type index.

    `max` over each column picks the lexicographically largest non-empty key, which is just "the key"
    for the normal case of one group per type per session, and is at least deterministic when a session
    somehow spans two.
    """
    tag_queries(team_id=team.id, product=Product.REPLAY_VISION, feature=Feature.QUERY)
    selects = ", ".join(f"max(`$group_{i}`) AS group_{i}" for i in GROUP_TYPE_INDEXES)
    query = parse_select(
        f"SELECT {selects} FROM events WHERE `$session_id` = {{session_id}} "
        "AND timestamp >= {start} AND timestamp <= {end}",
        placeholders={
            "session_id": ast.Constant(value=session_id),
            "start": ast.Constant(value=start - _TIMESTAMP_SLACK),
            "end": ast.Constant(value=end + _TIMESTAMP_SLACK),
        },
    )
    response = execute_hogql_query(query=query, team=team)
    if not response.results:
        return {}
    row = response.results[0]
    return {index: key for index, key in zip(GROUP_TYPE_INDEXES, row) if key}
