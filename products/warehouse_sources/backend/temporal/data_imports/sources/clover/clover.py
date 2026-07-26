import re
import time
import dataclasses
from datetime import UTC, date, datetime
from typing import Any, Optional

import requests
from dateutil import parser as dateutil_parser
from requests import PreparedRequest, Request, Response

from products.warehouse_sources.backend.temporal.data_imports.pipelines.pipeline.typings import SourceResponse
from products.warehouse_sources.backend.temporal.data_imports.sources.clover.settings import (
    CLOVER_ENDPOINTS,
    CLOVER_REGION_HOSTS,
    FILTER_WINDOW_MS,
    OAUTH_REFRESH_PATH,
    PAGE_SIZE,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.common.http import make_tracked_session
from products.warehouse_sources.backend.temporal.data_imports.sources.common.rest_source import (
    RESTAPIConfig,
    rest_api_resource,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.common.rest_source.auth import AuthConfigBase
from products.warehouse_sources.backend.temporal.data_imports.sources.common.rest_source.paginators import BasePaginator
from products.warehouse_sources.backend.temporal.data_imports.sources.common.rest_source.typing import (
    ClientConfig,
    Endpoint,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.common.resumable import ResumableSourceManager

# Merchant ids are Clover's own base-32 alphanumeric identifiers. Enforced before the id is
# interpolated into a request path so a crafted value can't traverse out of /v3/merchants/.
MERCHANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9]+$")

# Stable substring on permanent token-exchange failures, matched by `get_non_retryable_errors`.
TOKEN_ERROR_MARKER = "[clover_token_error]"

# Clover access tokens live ~30 minutes; re-mint a minute early so one isn't rejected mid-flight.
TOKEN_EXPIRY_BUFFER_SECONDS = 60
# Used when the refresh response omits its expiry field, so a long sync still re-mints.
DEFAULT_TOKEN_TTL_SECONDS = 15 * 60

CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 120
PROBE_READ_TIMEOUT_SECONDS = 15


@dataclasses.dataclass
class CloverResumeConfig:
    # CloverPaginator snapshot: {"offset": int, "window_start_ms": int | None}.
    paginator_state: dict[str, Any]


class CloverTokenError(Exception):
    """The OAuth v2 refresh exchange failed permanently — retrying won't fix it."""

    def __init__(self, message: str) -> None:
        super().__init__(f"{message} {TOKEN_ERROR_MARKER}")


def base_url(region: str) -> str:
    host = CLOVER_REGION_HOSTS.get(region)
    if host is None:
        raise ValueError(f"Unknown Clover region: {region}")
    return host


def _now_ms() -> int:
    return round(datetime.now(tz=UTC).timestamp() * 1000)


def to_epoch_ms(value: Any) -> Optional[int]:
    """Coerce an incremental watermark to the epoch milliseconds Clover's `filter` expects.

    The stored column is an integer, but a schema configured before that (or a hand-edited sync
    config) can hand back a datetime or ISO string, so accept both rather than filtering on a
    value Clover would reject.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        return round(dt.timestamp() * 1000)
    if isinstance(value, date):
        return round(datetime.combine(value, datetime.min.time(), tzinfo=UTC).timestamp() * 1000)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return to_epoch_ms(dateutil_parser.parse(value))
        except (ValueError, OverflowError):
            return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class CloverAuth(AuthConfigBase):
    """Bearer auth for both credential shapes Clover supports.

    A merchant-generated API token is permanent and sent as-is. An OAuth v2 install instead gets a
    short-lived access token, which this mints lazily from the customer's app id + refresh token
    and re-mints on expiry. Clover's refresh response declares an absolute
    ``access_token_expiration`` (epoch seconds) rather than a TTL.
    """

    def __init__(
        self,
        host: str,
        api_token: Optional[str] = None,
        client_id: Optional[str] = None,
        refresh_token: Optional[str] = None,
    ) -> None:
        self._host = host
        self._api_token = api_token
        self._client_id = client_id
        self._refresh_token = refresh_token
        self._access_token: Optional[str] = None
        self._expires_at: float = 0.0

    def __call__(self, request: PreparedRequest) -> PreparedRequest:
        request.headers["Authorization"] = f"Bearer {self.token()}"
        return request

    def secret_values(self) -> tuple[str, ...]:
        # The minted access token is included, but callers fix their redaction set at
        # construction — before the first mint — so it is only reliably masked by the tracked
        # transport's Authorization header denylist. Keep the token on that header.
        return tuple(v for v in (self._api_token, self._refresh_token, self._access_token) if v)

    def token(self) -> str:
        if self._api_token:
            return self._api_token
        if self._access_token is None or time.time() >= self._expires_at:
            self._mint()
        if self._access_token is None:
            raise CloverTokenError("Clover did not return an access token")
        return self._access_token

    def _mint(self) -> None:
        if not self._client_id or not self._refresh_token:
            raise CloverTokenError("A Clover app ID and refresh token are required to mint an access token")

        # capture=False: the response body carries the minted token, which the name-based sample
        # scrubbers can't recognise. Redirects are refused so the refresh token can't be bounced
        # to another origin. Transport retries still cover 429/5xx.
        session = make_tracked_session(
            redact_values=(self._refresh_token,),
            allow_redirects=False,
            capture=False,
        )
        response = session.post(
            f"{self._host}{OAUTH_REFRESH_PATH}",
            json={"client_id": self._client_id, "refresh_token": self._refresh_token},
            timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
        )

        if 300 <= response.status_code < 500 and response.status_code != 429:
            raise CloverTokenError(
                f"Clover rejected the OAuth token refresh (HTTP {response.status_code}). "
                "Check the app ID and refresh token, and that the merchant has not uninstalled the app."
            )
        response.raise_for_status()

        try:
            payload = response.json()
        except ValueError as e:
            raise CloverTokenError("Clover returned a non-JSON response from the OAuth token endpoint") from e

        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise CloverTokenError("The Clover OAuth token response contained no access_token")

        self._access_token = token
        expiration = payload.get("access_token_expiration")
        if isinstance(expiration, (int, float)) and not isinstance(expiration, bool):
            self._expires_at = float(expiration) - TOKEN_EXPIRY_BUFFER_SECONDS
        else:
            self._expires_at = time.time() + DEFAULT_TOKEN_TTL_SECONDS


class CloverPaginator(BasePaginator):
    """Offset/limit walk over one v3 collection, optionally split into time-filter windows.

    Clover pages with ``offset``/``limit`` and rejects any time-filtered query spanning more than
    90 days. A backfill therefore sends no ``filter`` at all — unfiltered queries aren't capped —
    and is one unbounded offset walk. An incremental run walks ``[watermark, now]`` as a chain of
    windows, restarting the offset at each boundary. Both bounds are inclusive because Clover has
    no strict ``<`` operator, so the next window opens a millisecond past the last one.
    """

    def __init__(self, page_size: int, window: Optional[tuple[str, int, int]] = None) -> None:
        super().__init__()
        self._page_size = page_size
        self._time_field: Optional[str] = window[0] if window else None
        self._window_start_ms: int = window[1] if window else 0
        self._end_ms: int = window[2] if window else 0
        self._offset = 0

    def _window_end_ms(self) -> int:
        return min(self._window_start_ms + FILTER_WINDOW_MS, self._end_ms)

    def _apply(self, request: Request) -> None:
        params = request.params if request.params is not None else {}
        params["limit"] = self._page_size
        params["offset"] = self._offset
        if self._time_field is not None:
            params["filter"] = [
                f"{self._time_field}>={self._window_start_ms}",
                f"{self._time_field}<={self._window_end_ms()}",
            ]
        request.params = params

    def init_request(self, request: Request) -> None:
        self._apply(request)

    def update_request(self, request: Request) -> None:
        self._apply(request)

    def update_state(self, response: Response, data: Optional[list[Any]] = None) -> None:
        if data is not None and len(data) >= self._page_size:
            self._offset += self._page_size
            self._has_next_page = True
            return

        # A short page ends the current window. Unwindowed walks are done at that point.
        if self._time_field is None:
            self._has_next_page = False
            return

        next_start = self._window_end_ms() + 1
        if next_start > self._end_ms:
            self._has_next_page = False
            return

        self._window_start_ms = next_start
        self._offset = 0
        self._has_next_page = True

    def get_resume_state(self) -> Optional[dict[str, Any]]:
        if not self._has_next_page:
            return None
        return {
            "offset": self._offset,
            "window_start_ms": self._window_start_ms if self._time_field is not None else None,
        }

    def set_resume_state(self, state: dict[str, Any]) -> None:
        offset = state.get("offset")
        if offset is not None:
            self._offset = int(offset)
        window_start_ms = state.get("window_start_ms")
        # A saved window is only meaningful if this run is windowed too; a resumed run that lost
        # its watermark restarts the unfiltered walk rather than filtering on a stale bound.
        if window_start_ms is not None and self._time_field is not None:
            self._window_start_ms = int(window_start_ms)
        self._has_next_page = True

    def __str__(self) -> str:
        return f"CloverPaginator(offset={self._offset}, window_start_ms={self._window_start_ms})"


def _tracked_session(auth: CloverAuth) -> requests.Session:
    """Session every Clover request runs on.

    ``capture=False``: orders, payments and customers carry names, emails, phone numbers and
    postal addresses — free-text PII the name-based scrubbers can't recognise, so response bodies
    stay out of HTTP sample capture (requests are still metered and logged).
    """
    return make_tracked_session(redact_values=auth.secret_values(), capture=False)


def _build_auth(
    host: str,
    api_token: Optional[str],
    client_id: Optional[str],
    refresh_token: Optional[str],
) -> CloverAuth:
    return CloverAuth(host=host, api_token=api_token, client_id=client_id, refresh_token=refresh_token)


def _merchant_path(merchant_id: str, path: str) -> str:
    if not MERCHANT_ID_PATTERN.match(merchant_id):
        raise ValueError("Clover merchant ID must be alphanumeric")
    return f"/v3/merchants/{merchant_id}/{path}"


def resolve_time_field(endpoint: str, incremental_field: Optional[str]) -> Optional[str]:
    """The Clover timestamp to window on, or ``None`` to walk unfiltered.

    Only the exact column the watermark was computed from may be pushed down: filtering on a
    different timestamp than the one the pipeline checkpoints would silently drop rows.
    """
    config = CLOVER_ENDPOINTS[endpoint]
    if incremental_field is None or incremental_field not in config.filterable_fields:
        return None
    return incremental_field


def validate_credentials(
    region: str,
    merchant_id: str,
    api_token: Optional[str] = None,
    client_id: Optional[str] = None,
    refresh_token: Optional[str] = None,
    accept_forbidden: bool = True,
) -> tuple[bool, str | None]:
    """One cheap probe of GET /v3/merchants/{mId} to confirm the credentials are genuine.

    ``accept_forbidden`` mirrors the source-create contract: a 403 means the token is real but the
    app wasn't granted that permission, which must not block connecting the source — per-table
    access is reported separately by `endpoint_permissions`.
    """
    if not merchant_id or not MERCHANT_ID_PATTERN.match(merchant_id):
        return False, "Clover merchant ID must be alphanumeric. Copy it from your Clover dashboard."
    if not api_token and not (client_id and refresh_token):
        return False, "Enter either a Clover API token or an app ID and refresh token."

    try:
        host = base_url(region)
    except ValueError as e:
        return False, str(e)

    auth = _build_auth(host, api_token, client_id, refresh_token)
    try:
        response = _tracked_session(auth).get(
            f"{host}/v3/merchants/{merchant_id}",
            auth=auth,
            timeout=(CONNECT_TIMEOUT_SECONDS, PROBE_READ_TIMEOUT_SECONDS),
        )
    except CloverTokenError as e:
        return False, str(e)
    except requests.exceptions.RequestException as e:
        return False, str(e)

    if response.status_code == 401:
        return False, "Clover rejected the credentials. Check the API token (or refresh token) and try again."
    if response.status_code == 403:
        if accept_forbidden:
            return True, None
        return False, "Your Clover app is missing the permissions needed to read this data."
    if response.status_code == 404:
        return False, f"Clover has no merchant {merchant_id} in the {region} region. Check the ID and the region."
    if not response.ok:
        return False, f"Clover API error: {response.status_code}"

    return True, None


def endpoint_permissions(
    region: str,
    merchant_id: str,
    endpoints: list[str],
    api_token: Optional[str] = None,
    client_id: Optional[str] = None,
    refresh_token: Optional[str] = None,
) -> dict[str, str | None]:
    """Per-endpoint read access, since a merchant grants Clover permissions entity by entity.

    Only an outright denial counts as missing permission — a throttle, 5xx or network blip leaves
    the table reported as reachable so a blip never hides a table the user can actually sync.
    """
    try:
        host = base_url(region)
    except ValueError:
        return dict.fromkeys(endpoints)

    auth = _build_auth(host, api_token, client_id, refresh_token)
    session = _tracked_session(auth)
    results: dict[str, str | None] = {}

    for name in endpoints:
        config = CLOVER_ENDPOINTS.get(name)
        if config is None:
            results[name] = None
            continue
        try:
            response = session.get(
                f"{host}{_merchant_path(merchant_id, config.path)}",
                params={"limit": 1},
                auth=auth,
                timeout=(CONNECT_TIMEOUT_SECONDS, PROBE_READ_TIMEOUT_SECONDS),
            )
        except (CloverTokenError, ValueError, requests.exceptions.RequestException):
            results[name] = None
            continue

        if response.status_code in (401, 403):
            results[name] = f"Your Clover app is not permitted to read {name}. Grant the matching read permission."
        else:
            results[name] = None

    return results


def clover_source(
    region: str,
    merchant_id: str,
    endpoint: str,
    team_id: int,
    job_id: str,
    resumable_source_manager: ResumableSourceManager[CloverResumeConfig],
    api_token: Optional[str] = None,
    client_id: Optional[str] = None,
    refresh_token: Optional[str] = None,
    incremental_field: Optional[str] = None,
    should_use_incremental_field: bool = False,
    db_incremental_field_last_value: Optional[Any] = None,
) -> SourceResponse:
    config = CLOVER_ENDPOINTS[endpoint]
    host = base_url(region)
    auth = _build_auth(host, api_token, client_id, refresh_token)

    watermark_ms = to_epoch_ms(db_incremental_field_last_value) if should_use_incremental_field else None
    time_field = resolve_time_field(endpoint, incremental_field) if watermark_ms is not None else None
    # No watermark means a first sync: skip the filter entirely so the backfill isn't subject to
    # the 90-day cap and walks all of history in one offset pass.
    window = (time_field, watermark_ms, _now_ms()) if time_field is not None and watermark_ms is not None else None

    client_config: ClientConfig = {
        "base_url": host,
        "headers": {"Accept": "application/json"},
        "auth": auth,
        "session": _tracked_session(auth),
        # The regional host is the only origin the bearer token may reach; pinning it (with
        # redirects refused) keeps a tampered response from retargeting the credential.
        "allowed_hosts": [],
        "allow_redirects": False,
        "request_timeout": (CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
    }

    endpoint_config: Endpoint = {
        "path": _merchant_path(merchant_id, config.path),
        "params": dict(config.params),
        "paginator": CloverPaginator(page_size=PAGE_SIZE, window=window),
        "data_selector": "elements",
        # Fail loud if the collection envelope changes rather than silently syncing 0 rows.
        "data_selector_required": True,
    }

    rest_config: RESTAPIConfig = {
        "client": client_config,
        "resources": [
            {
                "name": endpoint,
                "table_name": endpoint,
                "write_disposition": {"disposition": "merge", "strategy": "upsert"}
                if should_use_incremental_field
                else "replace",
                "endpoint": endpoint_config,
                "table_format": "delta",
            }
        ],
    }

    initial_paginator_state: Optional[dict[str, Any]] = None
    if resumable_source_manager.can_resume():
        resume = resumable_source_manager.load_state()
        if resume is not None:
            initial_paginator_state = resume.paginator_state

    def save_checkpoint(state: Optional[dict[str, Any]]) -> None:
        # Runs after each page is yielded and snapshots the NEXT page, so a crash between pages
        # re-fetches at most the page we already emitted (the merge key dedupes it).
        if state:
            resumable_source_manager.save_state(CloverResumeConfig(paginator_state=state))

    resource = rest_api_resource(
        rest_config,
        team_id,
        job_id,
        None,
        resume_hook=save_checkpoint,
        initial_paginator_state=initial_paginator_state,
    )

    return SourceResponse(
        name=endpoint,
        items=lambda: resource,
        primary_keys=config.primary_keys,
        # Clover v3 collections take no sort parameter and document no ordering, so rows do not
        # arrive in cursor order. "desc" is what keeps that honest: the pipeline then commits the
        # watermark only once the whole run completes, instead of checkpointing a mid-run maximum
        # that a later page could still fall behind. The server-side `filter` window — not row
        # order — is what bounds an incremental fetch.
        sort_mode="desc",
    )
