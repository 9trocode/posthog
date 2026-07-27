import re
import base64
import datetime
import dataclasses
from collections.abc import Iterator
from typing import Any, Optional
from urllib.parse import urlencode

import requests
from structlog.types import FilteringBoundLogger

from products.warehouse_sources.backend.temporal.data_imports.pipelines.pipeline.typings import SourceResponse
from products.warehouse_sources.backend.temporal.data_imports.sources.common.http import make_tracked_session
from products.warehouse_sources.backend.temporal.data_imports.sources.common.resumable import ResumableSourceManager
from products.warehouse_sources.backend.temporal.data_imports.sources.xero.settings import (
    TENANT_ID_COLUMN,
    TENANT_NAME_COLUMN,
    XERO_ENDPOINTS,
    XeroEndpointConfig,
)

XERO_TOKEN_URL = "https://identity.xero.com/connect/token"
XERO_CONNECTIONS_URL = "https://api.xero.com/connections"
XERO_API_BASE_URL = "https://api.xero.com/api.xro/2.0"

# Read-only scopes covering every resource in the endpoint catalog. Sent on the
# client-credentials grant (a Xero custom connection); the authorization-code grant already
# carries the scopes the user consented to, so refreshing does not need them.
READ_SCOPES = " ".join(
    [
        "accounting.transactions.read",
        "accounting.contacts.read",
        "accounting.settings.read",
        "accounting.journals.read",
    ]
)

PAGE_SIZE = 500
# Belt and braces against an endpoint that quietly ignores `page` and keeps answering.
MAX_PAGES = 20_000
REQUEST_TIMEOUT_SECONDS = 120

# `/Date(1573755038314+0000)/` — the .NET serialization Xero still emits for its UTC timestamps.
_DOTNET_DATE_RE = re.compile(r"^/Date\((-?\d+)(?:[+-]\d{4})?\)/$")


class XeroAuthError(Exception):
    pass


@dataclasses.dataclass
class XeroResumeConfig:
    tenant_index: int
    """Position in the connections list we were walking when the last batch was yielded."""
    cursor: int
    """Next page number (page mode) or JournalNumber offset (offset mode) to request."""


def _dotnet_date_to_iso(value: str) -> Optional[str]:
    match = _DOTNET_DATE_RE.match(value)
    if match is None:
        return None
    millis = int(match.group(1))
    moment = datetime.datetime.fromtimestamp(millis / 1000, tz=datetime.UTC)
    return moment.isoformat()


def normalize_dates(value: Any) -> Any:
    """Rewrite Xero's .NET date strings into ISO 8601, recursively.

    Xero returns UTC timestamps as ``/Date(<epoch millis>+0000)/`` at every level of the
    payload (including nested line items), which no downstream consumer can read as a
    timestamp — and ``UpdatedDateUTC`` in that shape would make the incremental watermark
    a lexicographic comparison of opaque strings.
    """
    if isinstance(value, str):
        return _dotnet_date_to_iso(value) or value
    if isinstance(value, list):
        return [normalize_dates(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_dates(item) for key, item in value.items()}
    return value


def format_modified_since(value: Any) -> Optional[str]:
    """Format an incremental watermark for the ``If-Modified-Since`` header.

    Xero documents the header as a UTC timestamp in ``yyyy-mm-ddThh:mm:ss`` form, so an
    offset-aware value is converted to UTC and its offset dropped rather than sent as-is.
    """
    if value is None:
        return None

    if isinstance(value, str):
        try:
            parsed = datetime.datetime.fromisoformat(value)
        except ValueError:
            return None
    elif isinstance(value, datetime.datetime):
        parsed = value
    elif isinstance(value, datetime.date):
        parsed = datetime.datetime.combine(value, datetime.time.min)
    else:
        return None

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(datetime.UTC).replace(tzinfo=None)

    return parsed.strftime("%Y-%m-%dT%H:%M:%S")


class XeroClient:
    """Minimal Xero client: mints an access token, resolves tenants, and reads collections.

    Access tokens live 30 minutes — shorter than a large backfill — so every request re-mints
    once on a 401 before giving up.
    """

    def __init__(self, client_id: str, client_secret: str, refresh_token: Optional[str] = None) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token or None
        secrets = tuple(secret for secret in (client_secret, self._refresh_token) if secret)
        # Both sessions disable HTTP sample capture: the token exchange returns the access token in
        # a plain `access_token` field, and the data responses carry financial and contact records
        # the generic scrubber would not strip. Traffic stays metered but is never sampled.
        self._token_session = make_tracked_session(redact_values=secrets, capture=False)
        self._session = make_tracked_session(
            headers={"Accept": "application/json"},
            redact_values=secrets,
            capture=False,
        )
        self._token: Optional[str] = None

    def _basic_auth_header(self) -> str:
        raw = f"{self._client_id}:{self._client_secret}".encode()
        return f"Basic {base64.b64encode(raw).decode()}"

    def mint_token(self) -> str:
        if self._refresh_token:
            payload = {"grant_type": "refresh_token", "refresh_token": self._refresh_token}
        else:
            payload = {"grant_type": "client_credentials", "scope": READ_SCOPES}

        response = self._token_session.post(
            XERO_TOKEN_URL,
            data=payload,
            headers={
                "Authorization": self._basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code in (400, 401):
            raise XeroAuthError(
                f"Xero rejected the credentials ({response.status_code}). Check the client ID and secret, "
                "and — for an authorization-code app — that the refresh token has not expired or been rotated."
            )
        response.raise_for_status()

        token = response.json().get("access_token")
        if not token:
            raise XeroAuthError("Xero did not return an access token")

        self._token = token
        return token

    @property
    def token(self) -> str:
        if self._token is None:
            return self.mint_token()
        return self._token

    def _request(self, url: str, tenant_id: Optional[str] = None) -> requests.Response:
        def send() -> requests.Response:
            headers = {"Authorization": f"Bearer {self.token}"}
            if tenant_id is not None:
                headers["Xero-Tenant-Id"] = tenant_id
            return self._session.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)

        response = send()
        if response.status_code == 401:
            self.mint_token()
            response = send()

        response.raise_for_status()
        return response

    def list_tenants(self, tenant_id: Optional[str] = None) -> list[dict[str, Any]]:
        """Organizations this connection can read, optionally narrowed to one.

        Xero has no implicit "current" organization — every Accounting API call must name one
        via the ``Xero-Tenant-Id`` header, and ``/connections`` is the only way to learn them.
        """
        payload = self._request(XERO_CONNECTIONS_URL).json()
        connections = payload if isinstance(payload, list) else []
        tenants = [
            connection
            for connection in connections
            if connection.get("tenantType", "ORGANISATION") == "ORGANISATION" and connection.get("tenantId")
        ]

        if tenant_id:
            tenants = [tenant for tenant in tenants if tenant["tenantId"] == tenant_id]
            if not tenants:
                raise XeroAuthError(f"Xero organization {tenant_id} is not connected to this app")

        return tenants

    def get_collection(
        self,
        endpoint: XeroEndpointConfig,
        tenant_id: str,
        params: dict[str, Any],
        modified_since: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        url = f"{XERO_API_BASE_URL}/{endpoint.path}"
        if params:
            url = f"{url}?{urlencode(params)}"

        def send() -> requests.Response:
            headers = {"Authorization": f"Bearer {self.token}", "Xero-Tenant-Id": tenant_id}
            if modified_since is not None:
                headers["If-Modified-Since"] = modified_since
            return self._session.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)

        response = send()
        if response.status_code == 401:
            self.mint_token()
            response = send()

        # `If-Modified-Since` makes an unchanged collection answer 304 with no body.
        if response.status_code == 304:
            return []

        response.raise_for_status()

        rows = response.json().get(endpoint.data_key) or []
        return rows if isinstance(rows, list) else []


def _decorate(rows: list[dict[str, Any]], tenant: dict[str, Any]) -> list[dict[str, Any]]:
    tenant_id = tenant["tenantId"]
    tenant_name = tenant.get("tenantName")
    return [
        {**normalize_dates(row), TENANT_ID_COLUMN: tenant_id, TENANT_NAME_COLUMN: tenant_name}
        for row in rows
        if isinstance(row, dict)
    ]


def _first_key(rows: list[dict[str, Any]], endpoint: XeroEndpointConfig) -> Optional[str]:
    if not rows:
        return None
    return str(rows[0].get(endpoint.primary_key[0]))


def _initial_cursor(endpoint: XeroEndpointConfig) -> int:
    # Pages are 1-based; the Journals offset is exclusive and starts below the first JournalNumber.
    return 1 if endpoint.pagination == "page" else 0


def _query_params(endpoint: XeroEndpointConfig, cursor: int) -> dict[str, Any]:
    if endpoint.pagination == "page":
        params: dict[str, Any] = {"page": cursor, "pageSize": PAGE_SIZE}
        if endpoint.incremental_field == "UpdatedDateUTC":
            # Pin the order so the pipeline's ascending watermark advances monotonically across
            # pages instead of trusting Xero's unspecified default ordering.
            params["order"] = "UpdatedDateUTC ASC"
        return params
    if endpoint.pagination == "offset":
        return {"offset": cursor}
    return {}


def _next_offset(rows: list[dict[str, Any]], current: int) -> int:
    numbers = [value for value in (row.get("JournalNumber") for row in rows) if isinstance(value, int)]
    return max(numbers) if numbers else current + len(rows)


def get_rows(
    client: XeroClient,
    endpoint_name: str,
    tenant_id: Optional[str],
    resumable_source_manager: ResumableSourceManager[XeroResumeConfig],
    logger: FilteringBoundLogger,
    modified_since: Optional[str] = None,
) -> Iterator[list[dict[str, Any]]]:
    endpoint = XERO_ENDPOINTS[endpoint_name]
    tenants = client.list_tenants(tenant_id)

    resume: Optional[XeroResumeConfig] = None
    if resumable_source_manager.can_resume():
        resume = resumable_source_manager.load_state()

    start_index = resume.tenant_index if resume else 0

    for index in range(start_index, len(tenants)):
        tenant = tenants[index]
        cursor = resume.cursor if resume and index == start_index else _initial_cursor(endpoint)
        previous_first_key: Optional[str] = None
        pages = 0

        while True:
            rows = client.get_collection(
                endpoint,
                tenant_id=tenant["tenantId"],
                params=_query_params(endpoint, cursor),
                modified_since=modified_since,
            )
            if not rows:
                break

            if endpoint.pagination == "page":
                first_key = _first_key(rows, endpoint)
                if first_key is not None and first_key == previous_first_key:
                    logger.warning(
                        "Xero returned an identical page — stopping to avoid an unbounded walk",
                        endpoint=endpoint.name,
                        page=cursor,
                    )
                    break
                previous_first_key = first_key

            yield _decorate(rows, tenant)

            if endpoint.pagination == "single":
                break

            cursor = cursor + 1 if endpoint.pagination == "page" else _next_offset(rows, cursor)
            # Checkpoint after the batch is yielded: a crash re-fetches from here and the merge
            # dedupes on the primary key, whereas checkpointing first would skip the batch.
            resumable_source_manager.save_state(XeroResumeConfig(tenant_index=index, cursor=cursor))

            pages += 1
            if pages >= MAX_PAGES:
                logger.warning(
                    "Xero page cap reached — stopping this organization early",
                    endpoint=endpoint.name,
                    tenant_id=tenant["tenantId"],
                    pages=pages,
                )
                break

        if index + 1 < len(tenants):
            resumable_source_manager.save_state(
                XeroResumeConfig(tenant_index=index + 1, cursor=_initial_cursor(endpoint))
            )

    resumable_source_manager.clear_state()


def xero_source(
    client_id: str,
    client_secret: str,
    refresh_token: Optional[str],
    tenant_id: Optional[str],
    endpoint_name: str,
    resumable_source_manager: ResumableSourceManager[XeroResumeConfig],
    logger: FilteringBoundLogger,
    db_incremental_field_last_value: Any = None,
) -> SourceResponse:
    endpoint = XERO_ENDPOINTS[endpoint_name]
    client = XeroClient(client_id=client_id, client_secret=client_secret, refresh_token=refresh_token)
    modified_since = format_modified_since(db_incremental_field_last_value) if endpoint.incremental_field else None

    return SourceResponse(
        name=endpoint.name,
        items=lambda: get_rows(
            client=client,
            endpoint_name=endpoint_name,
            tenant_id=tenant_id,
            resumable_source_manager=resumable_source_manager,
            logger=logger,
            modified_since=modified_since,
        ),
        primary_keys=[TENANT_ID_COLUMN, *endpoint.primary_key],
        partition_count=1,
        partition_size=1,
        partition_mode="datetime" if endpoint.partition_key else None,
        partition_format="month" if endpoint.partition_key else None,
        partition_keys=[endpoint.partition_key] if endpoint.partition_key else None,
        sort_mode="asc",
    )


def validate_credentials(
    client_id: str,
    client_secret: str,
    refresh_token: Optional[str],
    tenant_id: Optional[str],
) -> tuple[bool, Optional[str]]:
    client = XeroClient(client_id=client_id, client_secret=client_secret, refresh_token=refresh_token)
    try:
        tenants = client.list_tenants(tenant_id)
    except XeroAuthError as e:
        return False, str(e)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status in (401, 403):
            return False, "Xero rejected the credentials. Check the client ID, secret and granted scopes."
        return False, f"Could not reach Xero: {e}"
    except Exception as e:
        return False, f"Could not reach Xero: {e}"

    if not tenants:
        return False, "No Xero organizations are connected to this app. Authorize at least one organization."

    return True, None
