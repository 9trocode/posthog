import json
import base64
import datetime
from collections.abc import Iterable
from typing import Any, Optional, cast

import pytest
from unittest import mock

import structlog
from requests import HTTPError, Response
from structlog.types import FilteringBoundLogger

from products.warehouse_sources.backend.temporal.data_imports.sources.common.resumable import ResumableSourceManager
from products.warehouse_sources.backend.temporal.data_imports.sources.xero.settings import (
    TENANT_ID_COLUMN,
    TENANT_NAME_COLUMN,
    XERO_ENDPOINTS,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.xero.xero import (
    PAGE_SIZE,
    READ_SCOPES,
    XeroAuthError,
    XeroClient,
    XeroResumeConfig,
    _query_params,
    format_modified_since,
    get_rows,
    normalize_dates,
    validate_credentials,
    xero_source,
)

SESSION_PATCH = "products.warehouse_sources.backend.temporal.data_imports.sources.xero.xero.make_tracked_session"

LOGGER = cast(FilteringBoundLogger, structlog.get_logger(__name__))

TENANTS = [
    {"tenantId": "tenant-a", "tenantName": "Acme", "tenantType": "ORGANISATION"},
    {"tenantId": "tenant-b", "tenantName": "Beta", "tenantType": "ORGANISATION"},
]


class FakeResumeManager(ResumableSourceManager[XeroResumeConfig]):
    """In-memory stand-in for the Redis-backed manager."""

    def __init__(self, state: Optional[XeroResumeConfig] = None) -> None:
        self.state = state
        self.saved: list[XeroResumeConfig] = []
        self.cleared = False

    def can_resume(self) -> bool:
        return self.state is not None

    def load_state(self) -> Optional[XeroResumeConfig]:
        return self.state

    def save_state(self, data: XeroResumeConfig) -> None:
        self.saved.append(data)

    def clear_state(self) -> None:
        self.cleared = True


def _response(body: Any, status_code: int = 200) -> Response:
    response = Response()
    response.status_code = status_code
    response.url = "https://api.xero.com/api.xro/2.0/Invoices"
    response._content = json.dumps(body).encode()
    return response


def _token_response(token: str = "access-1") -> Response:
    return _response({"access_token": token, "expires_in": 1800})


def _collection(key: str, rows: list[dict[str, Any]]) -> Response:
    return _response({"Id": "req", "Status": "OK", key: rows})


def _wire(
    api_responses: Iterable[Response],
    token_responses: Optional[Iterable[Response]] = None,
    refresh_token: Optional[str] = None,
) -> tuple[XeroClient, mock.MagicMock, mock.MagicMock]:
    token_session = mock.MagicMock()
    token_session.post.side_effect = list(token_responses or [_token_response()])
    api_session = mock.MagicMock()
    api_session.get.side_effect = list(api_responses)

    with mock.patch(SESSION_PATCH, side_effect=[token_session, api_session]):
        client = XeroClient(client_id="cid", client_secret="sec", refresh_token=refresh_token)

    return client, api_session, token_session


def _urls(api_session: mock.MagicMock) -> list[str]:
    return [call.args[0] for call in api_session.get.call_args_list]


def _headers(api_session: mock.MagicMock) -> list[dict[str, str]]:
    return [call.kwargs["headers"] for call in api_session.get.call_args_list]


class TestXeroTransport:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("/Date(1573755038314+0000)/", "2019-11-14T18:10:38.314000+00:00"),
            ("/Date(0+0000)/", "1970-01-01T00:00:00+00:00"),
            ("/Date(1573755038314)/", "2019-11-14T18:10:38.314000+00:00"),
            # Offsets are decorative — Xero's epoch millis are already UTC.
            ("/Date(1573755038314+1300)/", "2019-11-14T18:10:38.314000+00:00"),
            ("2019-11-14T00:00:00", "2019-11-14T00:00:00"),
            ("not a date", "not a date"),
            ("/Date(abc)/", "/Date(abc)/"),
        ],
    )
    def test_normalize_dates_rewrites_dotnet_strings(self, raw: str, expected: str) -> None:
        assert normalize_dates(raw) == expected

    def test_normalize_dates_walks_nested_structures(self) -> None:
        row = {
            "InvoiceID": "inv-1",
            "UpdatedDateUTC": "/Date(1573755038314+0000)/",
            "LineItems": [{"Description": "x", "SoldDate": "/Date(0+0000)/"}],
            "Contact": {"Updated": "/Date(0+0000)/", "Name": "Acme"},
            "Total": 12.5,
            "Sent": True,
            "Void": None,
        }
        assert normalize_dates(row) == {
            "InvoiceID": "inv-1",
            "UpdatedDateUTC": "2019-11-14T18:10:38.314000+00:00",
            "LineItems": [{"Description": "x", "SoldDate": "1970-01-01T00:00:00+00:00"}],
            "Contact": {"Updated": "1970-01-01T00:00:00+00:00", "Name": "Acme"},
            "Total": 12.5,
            "Sent": True,
            "Void": None,
        }

    @pytest.mark.parametrize(
        "value, expected",
        [
            (None, None),
            (datetime.datetime(2024, 3, 1, 9, 30, 15), "2024-03-01T09:30:15"),
            # Offset-aware values are converted to UTC, since Xero reads the header as UTC.
            (
                datetime.datetime(2024, 3, 1, 9, 30, 15, tzinfo=datetime.timezone(datetime.timedelta(hours=13))),
                "2024-02-29T20:30:15",
            ),
            ("2024-03-01T09:30:15+00:00", "2024-03-01T09:30:15"),
            ("2024-03-01T09:30:15", "2024-03-01T09:30:15"),
            (datetime.date(2024, 3, 1), "2024-03-01T00:00:00"),
            ("not a timestamp", None),
            (42, None),
        ],
    )
    def test_format_modified_since(self, value: Any, expected: Optional[str]) -> None:
        assert format_modified_since(value) == expected

    def test_mint_token_uses_client_credentials_without_refresh_token(self) -> None:
        client, _, token_session = _wire([])
        assert client.mint_token() == "access-1"

        kwargs = token_session.post.call_args.kwargs
        assert kwargs["data"] == {"grant_type": "client_credentials", "scope": READ_SCOPES}
        expected_auth = base64.b64encode(b"cid:sec").decode()
        assert kwargs["headers"]["Authorization"] == f"Basic {expected_auth}"

    def test_mint_token_uses_refresh_grant_when_refresh_token_supplied(self) -> None:
        client, _, token_session = _wire([], refresh_token="refresh-1")
        client.mint_token()

        assert token_session.post.call_args.kwargs["data"] == {
            "grant_type": "refresh_token",
            "refresh_token": "refresh-1",
        }

    @pytest.mark.parametrize("status", [400, 401])
    def test_mint_token_raises_auth_error_on_rejection(self, status: int) -> None:
        client, _, _ = _wire([], token_responses=[_response({"error": "invalid_grant"}, status_code=status)])
        with pytest.raises(XeroAuthError):
            client.mint_token()

    def test_mint_token_raises_when_no_token_in_body(self) -> None:
        client, _, _ = _wire([], token_responses=[_response({})])
        with pytest.raises(XeroAuthError):
            client.mint_token()

    def test_expired_token_is_reminted_once_mid_sync(self) -> None:
        endpoint = XERO_ENDPOINTS["accounts"]
        client, api_session, token_session = _wire(
            [_response({}, status_code=401), _collection("Accounts", [{"AccountID": "a-1"}])],
            token_responses=[_token_response("access-1"), _token_response("access-2")],
        )

        rows = client.get_collection(endpoint, tenant_id="tenant-a", params={})

        assert rows == [{"AccountID": "a-1"}]
        assert token_session.post.call_count == 2
        assert [h["Authorization"] for h in _headers(api_session)] == ["Bearer access-1", "Bearer access-2"]

    def test_get_collection_returns_empty_on_304_not_modified(self) -> None:
        client, _, _ = _wire([_response({}, status_code=304)])
        assert client.get_collection(XERO_ENDPOINTS["invoices"], tenant_id="tenant-a", params={}) == []

    def test_get_collection_sends_tenant_and_modified_since_headers(self) -> None:
        client, api_session, _ = _wire([_collection("Invoices", [])])
        client.get_collection(
            XERO_ENDPOINTS["invoices"],
            tenant_id="tenant-a",
            params={"page": 1},
            modified_since="2024-03-01T09:30:15",
        )

        headers = _headers(api_session)[0]
        assert headers["Xero-Tenant-Id"] == "tenant-a"
        assert headers["If-Modified-Since"] == "2024-03-01T09:30:15"
        assert _urls(api_session)[0] == "https://api.xero.com/api.xro/2.0/Invoices?page=1"

    def test_get_collection_raises_on_error_status(self) -> None:
        client, _, _ = _wire([_response({"Message": "nope"}, status_code=403)])
        with pytest.raises(HTTPError):
            client.get_collection(XERO_ENDPOINTS["invoices"], tenant_id="tenant-a", params={})

    def test_list_tenants_drops_non_organisation_connections(self) -> None:
        client, _, _ = _wire(
            [
                _response(
                    [
                        *TENANTS,
                        {"tenantId": "prac-1", "tenantName": "Practice", "tenantType": "PRACTICE"},
                        {"tenantName": "no id"},
                    ]
                )
            ]
        )
        assert [t["tenantId"] for t in client.list_tenants()] == ["tenant-a", "tenant-b"]

    def test_list_tenants_narrows_to_requested_organization(self) -> None:
        client, _, _ = _wire([_response(TENANTS)])
        assert [t["tenantId"] for t in client.list_tenants("tenant-b")] == ["tenant-b"]

    def test_list_tenants_raises_when_requested_organization_absent(self) -> None:
        client, _, _ = _wire([_response(TENANTS)])
        with pytest.raises(XeroAuthError, match="not connected to this app"):
            client.list_tenants("tenant-z")

    @pytest.mark.parametrize(
        "endpoint_name, cursor, expected",
        [
            ("invoices", 3, {"page": 3, "pageSize": PAGE_SIZE, "order": "UpdatedDateUTC ASC"}),
            # Journals track changes on CreatedDateUTC and walk by JournalNumber offset.
            ("journals", 120, {"offset": 120}),
            ("accounts", 1, {}),
        ],
    )
    def test_query_params_per_pagination_mode(self, endpoint_name: str, cursor: int, expected: dict[str, Any]) -> None:
        assert _query_params(XERO_ENDPOINTS[endpoint_name], cursor) == expected

    def test_page_walk_stops_on_first_empty_page_and_stamps_tenant(self) -> None:
        client, api_session, _ = _wire(
            [
                _response([TENANTS[0]]),
                _collection("Invoices", [{"InvoiceID": "inv-1", "UpdatedDateUTC": "/Date(0+0000)/"}]),
                _collection("Invoices", [{"InvoiceID": "inv-2"}]),
                _collection("Invoices", []),
            ]
        )
        manager = FakeResumeManager()

        batches = list(get_rows(client, "invoices", None, manager, LOGGER))

        assert batches == [
            [
                {
                    "InvoiceID": "inv-1",
                    "UpdatedDateUTC": "1970-01-01T00:00:00+00:00",
                    TENANT_ID_COLUMN: "tenant-a",
                    TENANT_NAME_COLUMN: "Acme",
                }
            ],
            [{"InvoiceID": "inv-2", TENANT_ID_COLUMN: "tenant-a", TENANT_NAME_COLUMN: "Acme"}],
        ]
        assert [url.rsplit("?", 1)[-1] for url in _urls(api_session)[1:]] == [
            f"page=1&pageSize={PAGE_SIZE}&order=UpdatedDateUTC+ASC",
            f"page=2&pageSize={PAGE_SIZE}&order=UpdatedDateUTC+ASC",
            f"page=3&pageSize={PAGE_SIZE}&order=UpdatedDateUTC+ASC",
        ]
        assert manager.cleared is True

    def test_page_walk_stops_when_api_ignores_the_page_param(self) -> None:
        repeated = [{"InvoiceID": "inv-1"}]
        client, api_session, _ = _wire(
            [
                _response([TENANTS[0]]),
                _collection("Invoices", repeated),
                _collection("Invoices", repeated),
                # A third page would only be reached if the repeat guard failed.
                _collection("Invoices", repeated),
            ]
        )

        batches = list(get_rows(client, "invoices", None, FakeResumeManager(), LOGGER))

        assert len(batches) == 1
        assert api_session.get.call_count == 3

    def test_single_page_endpoint_makes_one_collection_request(self) -> None:
        client, api_session, _ = _wire(
            [_response([TENANTS[0]]), _collection("Accounts", [{"AccountID": "a-1"}])],
        )

        batches = list(get_rows(client, "accounts", None, FakeResumeManager(), LOGGER))

        assert batches == [[{"AccountID": "a-1", TENANT_ID_COLUMN: "tenant-a", TENANT_NAME_COLUMN: "Acme"}]]
        assert api_session.get.call_count == 2

    def test_journal_offset_advances_to_highest_journal_number(self) -> None:
        client, api_session, _ = _wire(
            [
                _response([TENANTS[0]]),
                _collection(
                    "Journals", [{"JournalID": "j-1", "JournalNumber": 1}, {"JournalID": "j-2", "JournalNumber": 7}]
                ),
                _collection("Journals", []),
            ]
        )
        manager = FakeResumeManager()

        list(get_rows(client, "journals", None, manager, LOGGER))

        assert [url.rsplit("?", 1)[-1] for url in _urls(api_session)[1:]] == ["offset=0", "offset=7"]
        assert manager.saved[0] == XeroResumeConfig(tenant_index=0, cursor=7)

    def test_state_is_saved_after_each_batch_and_cleared_at_the_end(self) -> None:
        client, _, _ = _wire(
            [
                _response([TENANTS[0]]),
                _collection("Invoices", [{"InvoiceID": "inv-1"}]),
                _collection("Invoices", [{"InvoiceID": "inv-2"}]),
                _collection("Invoices", []),
            ]
        )
        manager = FakeResumeManager()

        list(get_rows(client, "invoices", None, manager, LOGGER))

        assert manager.saved == [
            XeroResumeConfig(tenant_index=0, cursor=2),
            XeroResumeConfig(tenant_index=0, cursor=3),
        ]
        assert manager.cleared is True

    def test_resume_restarts_at_the_saved_organization_and_page(self) -> None:
        client, api_session, _ = _wire(
            [
                _response(TENANTS),
                _collection("Invoices", [{"InvoiceID": "inv-9"}]),
                _collection("Invoices", []),
            ]
        )
        manager = FakeResumeManager(XeroResumeConfig(tenant_index=1, cursor=4))

        batches = list(get_rows(client, "invoices", None, manager, LOGGER))

        assert batches[0][0][TENANT_ID_COLUMN] == "tenant-b"
        assert _urls(api_session)[1].endswith(f"page=4&pageSize={PAGE_SIZE}&order=UpdatedDateUTC+ASC")

    def test_second_organization_starts_from_the_first_page(self) -> None:
        client, api_session, _ = _wire(
            [
                _response(TENANTS),
                _collection("Invoices", [{"InvoiceID": "inv-1"}]),
                _collection("Invoices", []),
                _collection("Invoices", [{"InvoiceID": "inv-2"}]),
                _collection("Invoices", []),
            ]
        )
        manager = FakeResumeManager()

        batches = list(get_rows(client, "invoices", None, manager, LOGGER))

        assert [batch[0][TENANT_ID_COLUMN] for batch in batches] == ["tenant-a", "tenant-b"]
        assert XeroResumeConfig(tenant_index=1, cursor=1) in manager.saved
        assert [h["Xero-Tenant-Id"] for h in _headers(api_session)[1:]] == [
            "tenant-a",
            "tenant-a",
            "tenant-b",
            "tenant-b",
        ]

    def test_source_response_shape_for_a_paged_endpoint(self) -> None:
        with mock.patch(SESSION_PATCH, side_effect=[mock.MagicMock(), mock.MagicMock()]):
            response = xero_source(
                client_id="cid",
                client_secret="sec",
                refresh_token=None,
                tenant_id=None,
                endpoint_name="invoices",
                resumable_source_manager=FakeResumeManager(),
                logger=LOGGER,
            )

        assert response.name == "invoices"
        assert response.primary_keys == [TENANT_ID_COLUMN, "InvoiceID"]
        assert response.sort_mode == "asc"
        assert response.partition_keys is None

    def test_source_response_partitions_journals_on_creation_time(self) -> None:
        with mock.patch(SESSION_PATCH, side_effect=[mock.MagicMock(), mock.MagicMock()]):
            response = xero_source(
                client_id="cid",
                client_secret="sec",
                refresh_token=None,
                tenant_id=None,
                endpoint_name="journals",
                resumable_source_manager=FakeResumeManager(),
                logger=LOGGER,
            )

        assert response.primary_keys == [TENANT_ID_COLUMN, "JournalID"]
        assert response.partition_mode == "datetime"
        assert response.partition_keys == ["CreatedDateUTC"]
        assert response.partition_format == "month"

    @pytest.mark.parametrize(
        "endpoint_name, last_value, expected_header",
        [
            ("invoices", datetime.datetime(2024, 3, 1, 9, 30, 15), "2024-03-01T09:30:15"),
            ("invoices", None, None),
            # Endpoints without a change timestamp always do a full read.
            ("currencies", datetime.datetime(2024, 3, 1, 9, 30, 15), None),
        ],
    )
    def test_incremental_watermark_becomes_the_if_modified_since_header(
        self, endpoint_name: str, last_value: Any, expected_header: Optional[str]
    ) -> None:
        endpoint = XERO_ENDPOINTS[endpoint_name]
        token_session = mock.MagicMock()
        token_session.post.side_effect = [_token_response()]
        api_session = mock.MagicMock()
        api_session.get.side_effect = [_response([TENANTS[0]]), _collection(endpoint.data_key, [])]

        with mock.patch(SESSION_PATCH, side_effect=[token_session, api_session]):
            response = xero_source(
                client_id="cid",
                client_secret="sec",
                refresh_token=None,
                tenant_id=None,
                endpoint_name=endpoint_name,
                resumable_source_manager=FakeResumeManager(),
                logger=LOGGER,
                db_incremental_field_last_value=last_value,
            )
            list(cast(Iterable[Any], response.items()))

        assert _headers(api_session)[1].get("If-Modified-Since") == expected_header


class TestXeroValidateCredentials:
    def test_valid_credentials(self) -> None:
        with mock.patch.object(XeroClient, "list_tenants", return_value=TENANTS):
            assert validate_credentials("cid", "sec", None, None) == (True, None)

    def test_no_connected_organizations(self) -> None:
        with mock.patch.object(XeroClient, "list_tenants", return_value=[]):
            is_valid, message = validate_credentials("cid", "sec", None, None)
        assert is_valid is False
        assert message is not None and "No Xero organizations" in message

    def test_auth_error_message_is_surfaced(self) -> None:
        with mock.patch.object(XeroClient, "list_tenants", side_effect=XeroAuthError("Xero rejected the credentials")):
            is_valid, message = validate_credentials("cid", "sec", None, None)
        assert (is_valid, message) == (False, "Xero rejected the credentials")

    @pytest.mark.parametrize("status", [401, 403])
    def test_http_auth_failures_are_reported_as_bad_credentials(self, status: int) -> None:
        error = HTTPError(response=_response({}, status_code=status))
        with mock.patch.object(XeroClient, "list_tenants", side_effect=error):
            is_valid, message = validate_credentials("cid", "sec", None, None)
        assert is_valid is False
        assert message is not None and "rejected the credentials" in message

    def test_unexpected_failures_do_not_raise(self) -> None:
        with mock.patch.object(XeroClient, "list_tenants", side_effect=ValueError("boom")):
            is_valid, message = validate_credentials("cid", "sec", None, None)
        assert is_valid is False
        assert message is not None and "Could not reach Xero" in message
