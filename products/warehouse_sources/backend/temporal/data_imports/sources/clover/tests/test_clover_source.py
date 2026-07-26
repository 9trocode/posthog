import pytest
from unittest import mock

from posthog.schema import ReleaseStatus, SourceFieldInputConfig, SourceFieldSelectConfig

from products.warehouse_sources.backend.temporal.data_imports.sources.clover.clover import CloverResumeConfig
from products.warehouse_sources.backend.temporal.data_imports.sources.clover.settings import (
    CLOVER_ENDPOINTS,
    ENDPOINTS,
    FILTERABLE_TIME_FIELDS,
    INCREMENTAL_FIELDS,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.clover.source import CloverSource
from products.warehouse_sources.backend.temporal.data_imports.sources.common.resumable import ResumableSourceManager
from products.warehouse_sources.backend.temporal.data_imports.sources.generated_configs.clover import (
    CloverAuthTypeConfig,
    CloverSourceConfig,
)
from products.warehouse_sources.backend.types import ExternalDataSourceType, IncrementalFieldType

MERCHANT_ID = "6MRDFDQMRSSTZ"
SOURCE_MODULE = "products.warehouse_sources.backend.temporal.data_imports.sources.clover.source"


def _token_config() -> CloverSourceConfig:
    return CloverSourceConfig(
        merchant_id=MERCHANT_ID,
        region="na",
        auth_type=CloverAuthTypeConfig(selection="api_token", api_token="tok"),
    )


def _oauth_config() -> CloverSourceConfig:
    return CloverSourceConfig(
        merchant_id=MERCHANT_ID,
        region="eu",
        auth_type=CloverAuthTypeConfig(selection="oauth", client_id="app", refresh_token="refresh"),
    )


class TestCloverSource:
    def setup_method(self) -> None:
        self.source = CloverSource()
        self.team_id = 123
        self.config = _token_config()

    def test_source_type(self) -> None:
        assert self.source.source_type == ExternalDataSourceType.CLOVER

    def test_get_source_config(self) -> None:
        config = self.source.get_source_config

        assert config.name.value == "Clover"
        assert config.label == "Clover"
        assert config.releaseStatus == ReleaseStatus.ALPHA
        assert not config.unreleasedSource
        assert config.iconPath == "/static/services/clover.png"

    def test_fields_cover_region_merchant_and_auth(self) -> None:
        fields = self.source.get_source_config.fields
        assert [f.name for f in fields] == ["region", "merchant_id", "auth_type"]

        region = next(f for f in fields if isinstance(f, SourceFieldSelectConfig) and f.name == "region")
        assert [option.value for option in region.options] == ["na", "eu", "latam", "sandbox"]

        merchant = next(f for f in fields if isinstance(f, SourceFieldInputConfig))
        assert merchant.required is True
        assert merchant.secret is False

    @pytest.mark.parametrize(
        "option_value, expected_secret_fields",
        [
            ("api_token", {"api_token"}),
            ("oauth", {"refresh_token"}),
        ],
    )
    def test_auth_option_secrets_are_password_inputs(self, option_value: str, expected_secret_fields: set[str]) -> None:
        auth = next(
            f
            for f in self.source.get_source_config.fields
            if isinstance(f, SourceFieldSelectConfig) and f.name == "auth_type"
        )
        option = next(o for o in auth.options if o.value == option_value)
        assert option.fields is not None

        secrets = {
            f.name for f in option.fields if isinstance(f, SourceFieldInputConfig) and f.secret and f.name is not None
        }
        assert secrets == expected_secret_fields

    def test_auth_sub_fields_are_optional_so_either_option_validates(self) -> None:
        # The generator flattens every option's sub-fields into one config class, so marking any
        # of them required would make the other option's credentials mandatory too.
        auth = next(
            f
            for f in self.source.get_source_config.fields
            if isinstance(f, SourceFieldSelectConfig) and f.name == "auth_type"
        )
        for option in auth.options:
            for field in option.fields or []:
                assert isinstance(field, SourceFieldInputConfig)
                assert field.required is False

    @pytest.mark.parametrize(
        "observed_error",
        [
            "401 Client Error: Unauthorized for url: https://api.clover.com/v3/merchants/M/orders",
            "403 Client Error: Forbidden for url: https://api.eu.clover.com/v3/merchants/M/items",
            "Clover rejected the OAuth token refresh (HTTP 401). [clover_token_error]",
        ],
    )
    def test_non_retryable_errors_match_permanent_failures(self, observed_error: str) -> None:
        assert any(key in observed_error for key in self.source.get_non_retryable_errors())

    @pytest.mark.parametrize(
        "other_error",
        [
            "429 Client Error: Too Many Requests for url: https://api.clover.com/v3/merchants/M/orders",
            "500 Server Error for url: https://api.clover.com/v3/merchants/M/orders",
        ],
    )
    def test_non_retryable_errors_ignore_transient(self, other_error: str) -> None:
        assert not any(key in other_error for key in self.source.get_non_retryable_errors())

    def test_get_schemas(self) -> None:
        schemas = self.source.get_schemas(self.config, self.team_id)

        assert {schema.name for schema in schemas} == set(ENDPOINTS)
        incremental = {schema.name for schema in schemas if schema.supports_incremental}
        # Only the entities carrying a timestamp Clover will filter on server-side.
        assert incremental == {"orders", "payments", "refunds", "credits", "items"}
        # Resume replays the checkpointed page and windows share their boundary millisecond, so
        # append would duplicate rows.
        assert all(schema.supports_append is False for schema in schemas)

    @pytest.mark.parametrize("endpoint", list(ENDPOINTS))
    def test_advertised_cursors_are_server_filterable_integers(self, endpoint: str) -> None:
        for field in INCREMENTAL_FIELDS[endpoint]:
            assert field["field"] in FILTERABLE_TIME_FIELDS
            # Clover returns epoch milliseconds, so the stored column is an integer.
            assert field["field_type"] == IncrementalFieldType.Integer

    def test_orders_prefer_modified_time(self) -> None:
        # `_select_incremental_field` picks the update-tracking cursor, which must be the one
        # listed for orders so late edits aren't missed.
        assert INCREMENTAL_FIELDS["orders"][0]["field"] == "modifiedTime"

    @pytest.mark.parametrize("endpoint", list(ENDPOINTS))
    def test_primary_keys_are_globally_unique_ids(self, endpoint: str) -> None:
        # Every endpoint is a top-level merchant collection (no fan-out), so Clover's own id is
        # unique across the table.
        assert CLOVER_ENDPOINTS[endpoint].primary_keys == ["id"]

    def test_get_schemas_filtered_by_names(self) -> None:
        schemas = self.source.get_schemas(self.config, self.team_id, names=["orders"])
        assert [schema.name for schema in schemas] == ["orders"]

    def test_documented_tables_render_without_credentials(self) -> None:
        tables = self.source.get_documented_tables()
        assert {table["name"] for table in tables} == set(ENDPOINTS)
        assert all(table["description"] for table in tables)

    @pytest.mark.parametrize(
        "config_factory, expected",
        [
            (_token_config, {"api_token": "tok", "client_id": None, "refresh_token": None}),
            (_oauth_config, {"api_token": None, "client_id": "app", "refresh_token": "refresh"}),
        ],
    )
    @mock.patch(f"{SOURCE_MODULE}.validate_clover_credentials", return_value=(True, None))
    def test_validate_credentials_unpacks_the_selected_auth_option(
        self,
        mock_validate: mock.MagicMock,
        config_factory: object,
        expected: dict[str, str | None],
    ) -> None:
        config = config_factory()  # type: ignore[operator]

        assert self.source.validate_credentials(config, self.team_id) == (True, None)

        kwargs = mock_validate.call_args.kwargs
        assert {key: kwargs[key] for key in expected} == expected
        assert kwargs["merchant_id"] == MERCHANT_ID
        assert kwargs["region"] == config.region

    @pytest.mark.parametrize(
        "schema_name, expected_accept_forbidden",
        [(None, True), ("orders", False)],
    )
    @mock.patch(f"{SOURCE_MODULE}.validate_clover_credentials", return_value=(True, None))
    def test_forbidden_only_blocks_a_per_schema_check(
        self, mock_validate: mock.MagicMock, schema_name: str | None, expected_accept_forbidden: bool
    ) -> None:
        self.source.validate_credentials(self.config, self.team_id, schema_name=schema_name)
        assert mock_validate.call_args.kwargs["accept_forbidden"] is expected_accept_forbidden

    @mock.patch(f"{SOURCE_MODULE}.clover_endpoint_permissions", return_value={"orders": None})
    def test_get_endpoint_permissions_plumbs_credentials(self, mock_permissions: mock.MagicMock) -> None:
        assert self.source.get_endpoint_permissions(_oauth_config(), self.team_id, ["orders"]) == {"orders": None}

        kwargs = mock_permissions.call_args.kwargs
        assert kwargs["endpoints"] == ["orders"]
        assert kwargs["client_id"] == "app"
        assert kwargs["refresh_token"] == "refresh"
        assert kwargs["api_token"] is None

    def test_get_resumable_source_manager_binds_resume_config(self) -> None:
        manager = self.source.get_resumable_source_manager(mock.MagicMock())
        assert isinstance(manager, ResumableSourceManager)
        assert manager._data_class is CloverResumeConfig

    @mock.patch(f"{SOURCE_MODULE}.clover_source")
    def test_source_for_pipeline_plumbs_arguments(self, mock_source: mock.MagicMock) -> None:
        inputs = mock.MagicMock()
        inputs.schema_name = "orders"
        inputs.incremental_field = "modifiedTime"
        inputs.should_use_incremental_field = True
        inputs.db_incremental_field_last_value = 1_700_000_000_000
        manager = mock.MagicMock()

        self.source.source_for_pipeline(self.config, manager, inputs)

        kwargs = mock_source.call_args.kwargs
        assert kwargs["region"] == "na"
        assert kwargs["merchant_id"] == MERCHANT_ID
        assert kwargs["endpoint"] == "orders"
        assert kwargs["api_token"] == "tok"
        assert kwargs["resumable_source_manager"] is manager
        # The user's chosen cursor must reach the transport, not a per-endpoint default.
        assert kwargs["incremental_field"] == "modifiedTime"
        assert kwargs["db_incremental_field_last_value"] == 1_700_000_000_000

    @mock.patch(f"{SOURCE_MODULE}.clover_source")
    def test_source_for_pipeline_omits_last_value_on_full_refresh(self, mock_source: mock.MagicMock) -> None:
        inputs = mock.MagicMock()
        inputs.schema_name = "employees"
        inputs.incremental_field = None
        inputs.should_use_incremental_field = False
        inputs.db_incremental_field_last_value = 1_700_000_000_000

        self.source.source_for_pipeline(self.config, mock.MagicMock(), inputs)

        assert mock_source.call_args.kwargs["db_incremental_field_last_value"] is None
