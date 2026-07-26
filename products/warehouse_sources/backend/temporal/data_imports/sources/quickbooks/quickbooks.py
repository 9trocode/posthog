import dataclasses
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any, Optional
from urllib.parse import urlencode

import requests
from structlog.types import FilteringBoundLogger

from products.warehouse_sources.backend.temporal.data_imports.pipelines.pipeline.typings import SourceResponse
from products.warehouse_sources.backend.temporal.data_imports.sources.common.http import make_tracked_session
from products.warehouse_sources.backend.temporal.data_imports.sources.common.resumable import ResumableSourceManager
from products.warehouse_sources.backend.temporal.data_imports.sources.quickbooks.settings import (
    CREATE_TIME_FIELD,
    LAST_UPDATED_FIELD,
    LAST_UPDATED_QUERY_PATH,
    METADATA_KEY,
    QUICKBOOKS_ENTITIES,
    QuickBooksEntityConfig,
)

# Intuit hosts sandbox companies on a separate API domain; the OAuth token endpoint is shared.
QUICKBOOKS_HOSTS = {
    "production": "https://quickbooks.api.intuit.com",
    "sandbox": "https://sandbox-quickbooks.api.intuit.com",
}
INTUIT_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"

# Minor version of the Accounting API surface the queries below are written against. Omitting it
# pins the request to the oldest supported shape, which drops fields we want.
QUICKBOOKS_MINOR_VERSION = "65"

# MAXRESULTS caps at 1000; 500 keeps request counts low without making one page enormous for
# wide transaction entities that embed their full line items.
PAGE_SIZE = 500
REQUEST_TIMEOUT_SECONDS = 120

# Timestamp literals in the query language are ISO 8601; Intuit's own examples carry an offset.
_QUERY_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S+00:00"

_HOISTED_METADATA_FIELDS = (CREATE_TIME_FIELD, LAST_UPDATED_FIELD)


@dataclasses.dataclass
class QuickBooksResumeConfig:
    # `STARTPOSITION` is 1-based. The `WHERE` bound is saved alongside it because an offset only
    # identifies a row within the result set of the query that produced it.
    start_position: int
    since: Optional[str] = None


def _get_session(client_secret: str, refresh_token: str) -> requests.Session:
    return make_tracked_session(
        headers={"Accept": "application/json"},
        redact_values=(client_secret, refresh_token),
    )


def _host(environment: str) -> str:
    host = QUICKBOOKS_HOSTS.get(environment)
    if host is None:
        raise ValueError(f"Invalid QuickBooks environment: {environment}")
    return host


def company_url(environment: str, realm_id: str, api_version: str) -> str:
    return f"{_host(environment)}/{api_version}/company/{realm_id}"


def _mint_token(session: requests.Session, client_id: str, client_secret: str, refresh_token: str) -> str:
    """Exchange the customer's refresh token for a ~1h access token.

    Intuit rotates the refresh token on every exchange and keeps the previous one valid for 24h,
    so a sync always presents the stored token rather than the rotated one it just received.
    """
    response = session.post(
        INTUIT_TOKEN_URL,
        auth=(client_id, client_secret),
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def escape_query_literal(value: str) -> str:
    """Escape a string for a single-quoted literal in the QuickBooks query language."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def format_query_timestamp(value: Any) -> Optional[str]:
    """Render an incremental watermark as a UTC ISO 8601 literal, or `None` when unusable."""
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=UTC)
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time(), tzinfo=UTC)
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
    else:
        return None

    return parsed.astimezone(UTC).strftime(_QUERY_TIMESTAMP_FORMAT)


def build_query(
    entity: QuickBooksEntityConfig,
    since: Optional[str] = None,
    start_position: int = 1,
    page_size: int = PAGE_SIZE,
) -> str:
    """Build the QueryService statement for one page of an entity."""
    if entity.singleton:
        return f"SELECT * FROM {entity.name}"

    clauses = [f"SELECT * FROM {entity.name}"]
    if since is not None:
        clauses.append(f"WHERE {LAST_UPDATED_QUERY_PATH} > '{escape_query_literal(since)}'")
    # `ORDERBY` is one word in this dialect and defaults to ascending, which is the order the
    # pipeline's watermark checkpointing assumes.
    clauses.append(f"ORDERBY {LAST_UPDATED_QUERY_PATH}")
    clauses.append(f"STARTPOSITION {start_position}")
    clauses.append(f"MAXRESULTS {page_size}")
    return " ".join(clauses)


def extract_rows(body: dict[str, Any], entity_name: str) -> list[dict[str, Any]]:
    """Pull an entity's rows out of a `QueryResponse` body.

    An empty result set comes back as an absent key rather than an empty list, and singleton
    entities can come back as a bare object.
    """
    query_response = body.get("QueryResponse")
    if not isinstance(query_response, dict):
        return []

    rows = query_response.get(entity_name)
    if isinstance(rows, dict):
        return [rows]
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    return []


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Hoist the nested `MetaData` timestamps to the row root, leaving the rest of the row alone."""
    metadata = row.get(METADATA_KEY)
    if not isinstance(metadata, dict):
        return row

    normalized = dict(row)
    for key in _HOISTED_METADATA_FIELDS:
        value = metadata.get(key)
        if value is not None:
            normalized.setdefault(key, value)
    return normalized


def validate_credentials(
    environment: str,
    realm_id: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    api_version: str,
) -> bool:
    """Confirm the OAuth credentials mint a token that can read the given company."""
    try:
        session = _get_session(client_secret, refresh_token)
        token = _mint_token(session, client_id, client_secret, refresh_token)
        response = session.get(
            f"{company_url(environment, realm_id, api_version)}/query",
            params={"query": "SELECT * FROM CompanyInfo", "minorversion": QUICKBOOKS_MINOR_VERSION},
            headers={"Authorization": f"Bearer {token}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        return response.status_code == 200
    except Exception:
        return False


def get_rows(
    environment: str,
    realm_id: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    entity_name: str,
    api_version: str,
    logger: FilteringBoundLogger,
    resumable_source_manager: ResumableSourceManager[QuickBooksResumeConfig],
    should_use_incremental_field: bool = False,
    db_incremental_field_last_value: Optional[Any] = None,
) -> Iterator[list[dict[str, Any]]]:
    entity = QUICKBOOKS_ENTITIES[entity_name]
    session = _get_session(client_secret, refresh_token)
    base_url = company_url(environment, realm_id, api_version)
    token = _mint_token(session, client_id, client_secret, refresh_token)

    since = format_query_timestamp(db_incremental_field_last_value) if should_use_incremental_field else None
    start_position = 1

    resume_config = resumable_source_manager.load_state() if resumable_source_manager.can_resume() else None
    if resume_config is not None:
        start_position = resume_config.start_position
        # The saved offset only means anything against the query that produced it.
        since = resume_config.since
        logger.debug(f"QuickBooks: resuming {entity_name} from STARTPOSITION {start_position}")

    def run_query(query: str) -> list[dict[str, Any]]:
        nonlocal token
        url = f"{base_url}/query?{urlencode({'query': query, 'minorversion': QUICKBOOKS_MINOR_VERSION})}"

        def _do() -> requests.Response:
            return session.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

        response = _do()
        # Access tokens last ~1h; re-mint once if the sync outlives one.
        if response.status_code == 401:
            token = _mint_token(session, client_id, client_secret, refresh_token)
            response = _do()

        if not response.ok:
            logger.error(f"QuickBooks API error: status={response.status_code}, body={response.text}, query={query}")
            response.raise_for_status()

        return extract_rows(response.json(), entity_name)

    while True:
        rows = run_query(build_query(entity, since=since, start_position=start_position, page_size=PAGE_SIZE))

        if rows:
            yield [normalize_row(row) for row in rows]

        # A short page is the only end-of-results signal the dialect gives.
        if entity.singleton or len(rows) < PAGE_SIZE:
            break

        start_position += PAGE_SIZE
        # Save state AFTER yielding so a crash re-yields the last page (the merge dedupes on
        # primary key) instead of skipping it.
        resumable_source_manager.save_state(
            QuickBooksResumeConfig(start_position=start_position, since=since),
        )


def quickbooks_source(
    environment: str,
    realm_id: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    entity_name: str,
    api_version: str,
    logger: FilteringBoundLogger,
    resumable_source_manager: ResumableSourceManager[QuickBooksResumeConfig],
    should_use_incremental_field: bool = False,
    db_incremental_field_last_value: Optional[Any] = None,
) -> SourceResponse:
    entity = QUICKBOOKS_ENTITIES[entity_name]

    return SourceResponse(
        name=entity_name,
        items=lambda: get_rows(
            environment=environment,
            realm_id=realm_id,
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            entity_name=entity_name,
            api_version=api_version,
            logger=logger,
            resumable_source_manager=resumable_source_manager,
            should_use_incremental_field=should_use_incremental_field,
            db_incremental_field_last_value=db_incremental_field_last_value,
        ),
        primary_keys=[entity.primary_key],
        partition_count=1,
        partition_size=1,
        partition_mode="datetime" if entity.partition_key else None,
        partition_format="month" if entity.partition_key else None,
        partition_keys=[entity.partition_key] if entity.partition_key else None,
        # `ORDERBY Metadata.LastUpdatedTime` is ascending by default.
        sort_mode="asc",
    )
