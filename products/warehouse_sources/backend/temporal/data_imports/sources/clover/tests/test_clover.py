import json
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any, cast

import pytest
from unittest import mock

import requests
from requests import Response

from products.warehouse_sources.backend.temporal.data_imports.sources.clover.clover import (
    CloverAuth,
    CloverResumeConfig,
    CloverTokenError,
    base_url,
    clover_source,
    endpoint_permissions,
    resolve_time_field,
    to_epoch_ms,
    validate_credentials,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.clover.settings import (
    CLOVER_ENDPOINTS,
    ENDPOINTS,
    FILTER_WINDOW_MS,
    PAGE_SIZE,
)

# Every Clover request — the pipeline client session, the credential probe, the per-endpoint
# permission probes and the OAuth refresh — is built by make_tracked_session in the clover module.
SESSION_PATCH = "products.warehouse_sources.backend.temporal.data_imports.sources.clover.clover.make_tracked_session"
NOW_PATCH = "products.warehouse_sources.backend.temporal.data_imports.sources.clover.clover._now_ms"

MERCHANT_ID = "6MRDFDQMRSSTZ"
NOW_MS = 1_800_000_000_000


def _response(payload: Any, status_code: int = 200) -> Response:
    resp = Response()
    resp.status_code = status_code
    resp._content = json.dumps(payload).encode()
    return resp


def _collection(count: int, start: int = 0) -> Response:
    return _response({"elements": [{"id": f"row{start + i}", "modifiedTime": NOW_MS} for i in range(count)]})


def _make_manager(resume_state: CloverResumeConfig | None = None) -> mock.MagicMock:
    manager = mock.MagicMock()
    manager.can_resume.return_value = resume_state is not None
    manager.load_state.return_value = resume_state
    return manager


def _wire(session: mock.MagicMock, responses: list[Response]) -> list[dict[str, Any]]:
    """Snapshot each request's url + params at prepare time.

    ``request.params`` is one dict mutated in place across pages, so reading it after the run
    would only ever show the final window/offset.
    """
    session.headers = {}
    snapshots: list[dict[str, Any]] = []

    def _prepare(request: Any) -> mock.MagicMock:
        snapshots.append({"url": request.url, "params": dict(request.params or {})})
        return mock.MagicMock(url=request.url)

    session.prepare_request.side_effect = _prepare
    session.send.side_effect = responses
    return snapshots


def _source(endpoint: str, manager: mock.MagicMock, **kwargs: Any) -> Any:
    return clover_source(
        region="na",
        merchant_id=MERCHANT_ID,
        endpoint=endpoint,
        team_id=1,
        job_id="job-1",
        resumable_source_manager=manager,
        api_token="tok",
        **kwargs,
    )


def _rows(source_response: Any) -> list[dict[str, Any]]:
    return [row for page in cast(Iterable[Any], source_response.items()) for row in page]


class TestCloverTransport:
    @pytest.mark.parametrize(
        "value, expected",
        [
            (None, None),
            (1_700_000_000_000, 1_700_000_000_000),
            ("1700000000000", 1_700_000_000_000),
            (datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC), 1_700_000_000_000),
            (datetime(2023, 11, 14, 22, 13, 20), 1_700_000_000_000),
            (date(2023, 11, 14), 1_699_920_000_000),
            ("2023-11-14T22:13:20Z", 1_700_000_000_000),
            ("not-a-time", None),
            (True, None),
        ],
    )
    def test_to_epoch_ms(self, value: Any, expected: int | None) -> None:
        assert to_epoch_ms(value) == expected

    @pytest.mark.parametrize(
        "region, expected",
        [
            ("na", "https://api.clover.com"),
            ("eu", "https://api.eu.clover.com"),
            ("latam", "https://api.clover.com.br"),
            ("sandbox", "https://apisandbox.dev.clover.com"),
        ],
    )
    def test_base_url_per_region(self, region: str, expected: str) -> None:
        assert base_url(region) == expected

    def test_base_url_rejects_unknown_region(self) -> None:
        with pytest.raises(ValueError, match="Unknown Clover region"):
            base_url("mars")

    @pytest.mark.parametrize(
        "endpoint, requested, expected",
        [
            ("orders", "modifiedTime", "modifiedTime"),
            ("orders", "createdTime", "createdTime"),
            # A cursor Clover cannot filter on must not be pushed down as some other column's
            # window, or rows between the two timestamps would be skipped.
            ("orders", "clientCreatedTime", None),
            ("orders", None, None),
            ("customers", "customerSince", None),
            ("items", "createdTime", None),
        ],
    )
    def test_resolve_time_field(self, endpoint: str, requested: str | None, expected: str | None) -> None:
        assert resolve_time_field(endpoint, requested) == expected

    @mock.patch(SESSION_PATCH)
    def test_merchant_id_must_be_alphanumeric(self, mock_session: mock.MagicMock) -> None:
        with pytest.raises(ValueError, match="alphanumeric"):
            clover_source(
                region="na",
                merchant_id="../../oauth",
                endpoint="orders",
                team_id=1,
                job_id="job-1",
                resumable_source_manager=_make_manager(),
                api_token="tok",
            )

    @mock.patch(SESSION_PATCH)
    def test_capture_disabled_and_token_redacted(self, mock_session: mock.MagicMock) -> None:
        _wire(mock_session.return_value, [_collection(1)])
        _rows(_source("orders", _make_manager()))

        assert mock_session.call_args_list
        for call in mock_session.call_args_list:
            assert call.kwargs.get("capture") is False
            assert "tok" in call.kwargs.get("redact_values", ())


class TestPagination:
    @mock.patch(SESSION_PATCH)
    def test_full_refresh_walks_offsets_and_sends_no_filter(self, mock_session: mock.MagicMock) -> None:
        snapshots = _wire(mock_session.return_value, [_collection(PAGE_SIZE), _collection(3, start=PAGE_SIZE)])

        manager = _make_manager()
        rows = _rows(_source("orders", manager))

        assert len(rows) == PAGE_SIZE + 3
        assert snapshots[0]["url"].endswith(f"/v3/merchants/{MERCHANT_ID}/orders")
        assert snapshots[0]["params"]["limit"] == PAGE_SIZE
        assert snapshots[0]["params"]["offset"] == 0
        assert snapshots[0]["params"]["expand"] == "lineItems"
        assert "filter" not in snapshots[0]["params"]
        assert snapshots[1]["params"]["offset"] == PAGE_SIZE
        # Only the page that still had a successor is checkpointed.
        assert manager.save_state.call_count == 1
        assert manager.save_state.call_args.args[0] == CloverResumeConfig(
            paginator_state={"offset": PAGE_SIZE, "window_start_ms": None}
        )

    @mock.patch(SESSION_PATCH)
    def test_short_first_page_stops_immediately(self, mock_session: mock.MagicMock) -> None:
        session = mock_session.return_value
        _wire(session, [_collection(2)])

        manager = _make_manager()
        rows = _rows(_source("employees", manager))

        assert session.send.call_count == 1
        assert len(rows) == 2
        manager.save_state.assert_not_called()

    @mock.patch(SESSION_PATCH)
    def test_resumes_from_saved_offset(self, mock_session: mock.MagicMock) -> None:
        snapshots = _wire(mock_session.return_value, [_collection(1)])

        _rows(_source("orders", _make_manager(CloverResumeConfig(paginator_state={"offset": 3000}))))

        assert snapshots[0]["params"]["offset"] == 3000

    @mock.patch(SESSION_PATCH)
    def test_first_sync_without_watermark_is_unfiltered(self, mock_session: mock.MagicMock) -> None:
        snapshots = _wire(mock_session.return_value, [_collection(1)])

        _rows(
            _source(
                "orders",
                _make_manager(),
                incremental_field="modifiedTime",
                should_use_incremental_field=True,
                db_incremental_field_last_value=None,
            )
        )

        # An unfiltered query is not subject to Clover's 90-day cap, so the backfill is one walk.
        assert "filter" not in snapshots[0]["params"]


class TestIncrementalWindowing:
    @mock.patch(NOW_PATCH, return_value=NOW_MS)
    @mock.patch(SESSION_PATCH)
    def test_windows_are_capped_and_chained(self, mock_session: mock.MagicMock, _now: mock.MagicMock) -> None:
        watermark = NOW_MS - (FILTER_WINDOW_MS * 2) - 5000
        # One short page per window: three windows to cover slightly over 2x the window size.
        snapshots = _wire(mock_session.return_value, [_collection(1), _collection(1), _collection(1)])

        _rows(
            _source(
                "orders",
                _make_manager(),
                incremental_field="modifiedTime",
                should_use_incremental_field=True,
                db_incremental_field_last_value=watermark,
            )
        )

        assert [snap["params"]["filter"] for snap in snapshots] == [
            [f"modifiedTime>={watermark}", f"modifiedTime<={watermark + FILTER_WINDOW_MS}"],
            [
                f"modifiedTime>={watermark + FILTER_WINDOW_MS + 1}",
                f"modifiedTime<={watermark + FILTER_WINDOW_MS * 2 + 1}",
            ],
            [f"modifiedTime>={watermark + FILTER_WINDOW_MS * 2 + 2}", f"modifiedTime<={NOW_MS}"],
        ]

    @mock.patch(NOW_PATCH, return_value=NOW_MS)
    @mock.patch(SESSION_PATCH)
    def test_single_window_terminates_after_one_page(self, mock_session: mock.MagicMock, _now: mock.MagicMock) -> None:
        session = mock_session.return_value
        _wire(session, [_collection(1)])

        _rows(
            _source(
                "payments",
                _make_manager(),
                incremental_field="createdTime",
                should_use_incremental_field=True,
                db_incremental_field_last_value=NOW_MS - 1000,
            )
        )

        # The watermark is inside one window, so the walk must stop rather than loop on it.
        assert session.send.call_count == 1

    @mock.patch(NOW_PATCH, return_value=NOW_MS)
    @mock.patch(SESSION_PATCH)
    def test_offset_restarts_at_each_window_boundary(self, mock_session: mock.MagicMock, _now: mock.MagicMock) -> None:
        watermark = NOW_MS - (FILTER_WINDOW_MS + 5000)
        snapshots = _wire(mock_session.return_value, [_collection(PAGE_SIZE), _collection(1), _collection(1)])

        _rows(
            _source(
                "orders",
                _make_manager(),
                incremental_field="modifiedTime",
                should_use_incremental_field=True,
                db_incremental_field_last_value=watermark,
            )
        )

        assert [snap["params"]["offset"] for snap in snapshots] == [0, PAGE_SIZE, 0]

    @mock.patch(NOW_PATCH, return_value=NOW_MS)
    @mock.patch(SESSION_PATCH)
    def test_checkpoints_and_resumes_the_window(self, mock_session: mock.MagicMock, _now: mock.MagicMock) -> None:
        watermark = NOW_MS - (FILTER_WINDOW_MS + 5000)
        _wire(mock_session.return_value, [_collection(1), _collection(1)])

        manager = _make_manager()
        _rows(
            _source(
                "orders",
                manager,
                incremental_field="modifiedTime",
                should_use_incremental_field=True,
                db_incremental_field_last_value=watermark,
            )
        )
        assert manager.save_state.call_args_list[0].args[0] == CloverResumeConfig(
            paginator_state={"offset": 0, "window_start_ms": watermark + FILTER_WINDOW_MS + 1}
        )

        resumed = _wire(mock_session.return_value, [_collection(1)])
        _rows(
            _source(
                "orders",
                _make_manager(CloverResumeConfig(paginator_state={"offset": 2000, "window_start_ms": NOW_MS - 1000})),
                incremental_field="modifiedTime",
                should_use_incremental_field=True,
                db_incremental_field_last_value=watermark,
            )
        )
        assert resumed[0]["params"]["offset"] == 2000
        assert resumed[0]["params"]["filter"] == [f"modifiedTime>={NOW_MS - 1000}", f"modifiedTime<={NOW_MS}"]

    @mock.patch(SESSION_PATCH)
    def test_saved_window_ignored_when_run_is_unfiltered(self, mock_session: mock.MagicMock) -> None:
        snapshots = _wire(mock_session.return_value, [_collection(1)])

        # A run that lost its watermark must not resurrect a stale window bound from Redis.
        _rows(
            _source(
                "orders",
                _make_manager(CloverResumeConfig(paginator_state={"offset": 10, "window_start_ms": 123})),
            )
        )

        assert snapshots[0]["params"]["offset"] == 10
        assert "filter" not in snapshots[0]["params"]


class TestResponseShape:
    @mock.patch(SESSION_PATCH)
    def test_missing_elements_envelope_fails_loud(self, mock_session: mock.MagicMock) -> None:
        _wire(mock_session.return_value, [_response([{"id": "a"}])])

        with pytest.raises(ValueError, match="data_selector"):
            _rows(_source("orders", _make_manager()))

    @pytest.mark.parametrize("endpoint", list(ENDPOINTS))
    @mock.patch(SESSION_PATCH)
    def test_source_response_metadata(self, mock_session: mock.MagicMock, endpoint: str) -> None:
        response = _source(endpoint, _make_manager())

        assert response.name == endpoint
        assert response.primary_keys == CLOVER_ENDPOINTS[endpoint].primary_keys
        # Clover collections document no ordering, so the watermark may only be committed once the
        # whole run finishes.
        assert response.sort_mode == "desc"


class TestCloverAuth:
    @mock.patch(SESSION_PATCH)
    def test_api_token_is_used_verbatim(self, mock_session: mock.MagicMock) -> None:
        auth = CloverAuth(host="https://api.clover.com", api_token="tok")
        assert auth.token() == "tok"
        mock_session.assert_not_called()

    @mock.patch(SESSION_PATCH)
    def test_refresh_token_mints_and_caches(self, mock_session: mock.MagicMock) -> None:
        mock_session.return_value.post.return_value = _response(
            {"access_token": "minted", "access_token_expiration": 2_000_000_000}
        )
        auth = CloverAuth(host="https://api.clover.com", client_id="app", refresh_token="refresh")

        assert auth.token() == "minted"
        assert auth.token() == "minted"
        # A live token is reused rather than re-minted on every request.
        assert mock_session.return_value.post.call_count == 1
        call = mock_session.return_value.post.call_args
        assert call.args[0] == "https://api.clover.com/oauth/v2/refresh"
        assert call.kwargs["json"] == {"client_id": "app", "refresh_token": "refresh"}
        assert mock_session.call_args.kwargs["allow_redirects"] is False
        assert mock_session.call_args.kwargs["capture"] is False

    @mock.patch("time.time", return_value=2_000_000_000.0)
    @mock.patch(SESSION_PATCH)
    def test_expired_token_is_reminted(self, mock_session: mock.MagicMock, _time: mock.MagicMock) -> None:
        mock_session.return_value.post.side_effect = [
            _response({"access_token": "first", "access_token_expiration": 2_000_000_000}),
            _response({"access_token": "second", "access_token_expiration": 2_100_000_000}),
        ]
        auth = CloverAuth(host="https://api.clover.com", client_id="app", refresh_token="refresh")

        assert auth.token() == "first"
        # The first token's absolute expiry is already in the past once the buffer is applied.
        assert auth.token() == "second"

    @mock.patch(SESSION_PATCH)
    def test_missing_expiry_falls_back_to_short_ttl(self, mock_session: mock.MagicMock) -> None:
        mock_session.return_value.post.return_value = _response({"access_token": "minted"})
        auth = CloverAuth(host="https://api.clover.com", client_id="app", refresh_token="refresh")

        assert auth.token() == "minted"
        assert auth.token() == "minted"
        assert mock_session.return_value.post.call_count == 1

    @pytest.mark.parametrize("status_code", [400, 401, 403, 404])
    @mock.patch(SESSION_PATCH)
    def test_permanent_refresh_failures_raise_marked_error(
        self, mock_session: mock.MagicMock, status_code: int
    ) -> None:
        mock_session.return_value.post.return_value = _response({"message": "nope"}, status_code=status_code)
        auth = CloverAuth(host="https://api.clover.com", client_id="app", refresh_token="refresh")

        with pytest.raises(CloverTokenError, match=r"\[clover_token_error\]"):
            auth.token()

    @mock.patch(SESSION_PATCH)
    def test_token_response_without_access_token_raises(self, mock_session: mock.MagicMock) -> None:
        mock_session.return_value.post.return_value = _response({"refresh_token": "only"})
        auth = CloverAuth(host="https://api.clover.com", client_id="app", refresh_token="refresh")

        with pytest.raises(CloverTokenError, match="no access_token"):
            auth.token()

    def test_missing_oauth_inputs_raise_before_any_request(self) -> None:
        with pytest.raises(CloverTokenError, match="app ID and refresh token"):
            CloverAuth(host="https://api.clover.com", client_id="app").token()

    @mock.patch(SESSION_PATCH)
    def test_secret_values_cover_every_credential(self, mock_session: mock.MagicMock) -> None:
        mock_session.return_value.post.return_value = _response({"access_token": "minted"})
        auth = CloverAuth(host="https://api.clover.com", client_id="app", refresh_token="refresh")
        auth.token()

        assert set(auth.secret_values()) == {"refresh", "minted"}


class TestValidateCredentials:
    @pytest.mark.parametrize(
        "status_code, accept_forbidden, expected_valid",
        [
            (200, True, True),
            (401, True, False),
            (403, True, True),
            (403, False, False),
            (404, True, False),
            (500, True, False),
        ],
    )
    @mock.patch(SESSION_PATCH)
    def test_status_mapping(
        self, mock_session: mock.MagicMock, status_code: int, accept_forbidden: bool, expected_valid: bool
    ) -> None:
        mock_session.return_value.get.return_value = _response({"id": MERCHANT_ID}, status_code=status_code)

        valid, _ = validate_credentials(
            region="na", merchant_id=MERCHANT_ID, api_token="tok", accept_forbidden=accept_forbidden
        )
        assert valid is expected_valid

    @pytest.mark.parametrize(
        "merchant_id, api_token, client_id, refresh_token, expected_error",
        [
            ("../evil", "tok", None, None, "alphanumeric"),
            ("", "tok", None, None, "alphanumeric"),
            (MERCHANT_ID, None, None, None, "API token"),
            (MERCHANT_ID, None, "app", None, "API token"),
        ],
    )
    @mock.patch(SESSION_PATCH)
    def test_rejects_bad_input_without_calling_the_api(
        self,
        mock_session: mock.MagicMock,
        merchant_id: str,
        api_token: str | None,
        client_id: str | None,
        refresh_token: str | None,
        expected_error: str,
    ) -> None:
        valid, message = validate_credentials(
            region="na",
            merchant_id=merchant_id,
            api_token=api_token,
            client_id=client_id,
            refresh_token=refresh_token,
        )

        assert valid is False
        assert message is not None and expected_error in message
        mock_session.assert_not_called()

    @mock.patch(SESSION_PATCH)
    def test_unknown_region_is_reported(self, mock_session: mock.MagicMock) -> None:
        valid, message = validate_credentials(region="mars", merchant_id=MERCHANT_ID, api_token="tok")
        assert valid is False
        assert message == "Unknown Clover region: mars"

    @mock.patch(SESSION_PATCH)
    def test_token_refresh_failure_is_reported(self, mock_session: mock.MagicMock) -> None:
        mock_session.return_value.post.return_value = _response({}, status_code=401)
        mock_session.return_value.get.side_effect = lambda *a, **kw: kw["auth"].token()

        valid, message = validate_credentials(
            region="na", merchant_id=MERCHANT_ID, client_id="app", refresh_token="refresh"
        )
        assert valid is False
        assert message is not None and "clover_token_error" in message

    @mock.patch(SESSION_PATCH)
    def test_network_error_is_reported(self, mock_session: mock.MagicMock) -> None:
        mock_session.return_value.get.side_effect = requests.exceptions.ConnectionError("boom")
        valid, message = validate_credentials(region="na", merchant_id=MERCHANT_ID, api_token="tok")
        assert valid is False
        assert message == "boom"


class TestEndpointPermissions:
    @mock.patch(SESSION_PATCH)
    def test_only_denials_are_reported(self, mock_session: mock.MagicMock) -> None:
        mock_session.return_value.get.side_effect = [
            _response({"elements": []}),
            _response({"message": "denied"}, status_code=403),
            _response({"message": "boom"}, status_code=500),
        ]

        result = endpoint_permissions(
            region="na",
            merchant_id=MERCHANT_ID,
            endpoints=["orders", "payments", "items"],
            api_token="tok",
        )

        assert result["orders"] is None
        assert result["payments"] is not None and "payments" in result["payments"]
        # A 5xx is a blip, not a missing grant — it must not hide a syncable table.
        assert result["items"] is None

    @mock.patch(SESSION_PATCH)
    def test_probe_uses_a_single_row_and_the_merchant_path(self, mock_session: mock.MagicMock) -> None:
        mock_session.return_value.get.return_value = _response({"elements": []})

        endpoint_permissions(region="na", merchant_id=MERCHANT_ID, endpoints=["orders"], api_token="tok")

        call = mock_session.return_value.get.call_args
        assert call.args[0] == f"https://api.clover.com/v3/merchants/{MERCHANT_ID}/orders"
        assert call.kwargs["params"] == {"limit": 1}

    @mock.patch(SESSION_PATCH)
    def test_unknown_endpoints_and_bad_merchant_id_do_not_block(self, mock_session: mock.MagicMock) -> None:
        mock_session.return_value.get.return_value = _response({"elements": []})

        assert endpoint_permissions(region="na", merchant_id="../evil", endpoints=["orders"], api_token="tok") == {
            "orders": None
        }
        assert endpoint_permissions(region="mars", merchant_id=MERCHANT_ID, endpoints=["orders"], api_token="tok") == {
            "orders": None
        }
