import pytest
from unittest import mock

from posthog.schema import (
    DataWarehouseSourceCategory,
    ReleaseStatus,
    SourceFieldInputConfig,
    SourceFieldInputConfigType,
    SourceFieldSelectConfig,
)

from products.warehouse_sources.backend.temporal.data_imports.sources.common.resumable import ResumableSourceManager
from products.warehouse_sources.backend.temporal.data_imports.sources.generated_configs.quickbooks import (
    QuickBooksSourceConfig,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.quickbooks.quickbooks import (
    QuickBooksResumeConfig,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.quickbooks.settings import (
    ENDPOINTS,
    INCREMENTAL_FIELDS,
    QUICKBOOKS_ENTITIES,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.quickbooks.source import QuickBooksSource
from products.warehouse_sources.backend.types import ExternalDataSourceType

_SOURCE_MODULE = "products.warehouse_sources.backend.temporal.data_imports.sources.quickbooks.source"


class TestQuickBooksSource:
    def setup_method(self) -> None:
        self.source = QuickBooksSource()
        self.team_id = 123
        self.config = QuickBooksSourceConfig(
            realm_id="123456789",
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="refresh-token",
            environment="production",
        )

    def test_source_type(self) -> None:
        assert self.source.source_type == ExternalDataSourceType.QUICKBOOKS

    def test_get_source_config(self) -> None:
        config = self.source.get_source_config

        assert config.name.value == "QuickBooks"
        assert config.label == "QuickBooks"
        assert config.category == DataWarehouseSourceCategory.FINANCE___ACCOUNTING
        assert config.releaseStatus == ReleaseStatus.ALPHA
        assert config.unreleasedSource is None
        assert config.iconPath == "/static/services/quickbooks.png"
        assert config.docsUrl == "https://posthog.com/docs/cdp/sources/quickbooks"

    def test_config_fields(self) -> None:
        config = self.source.get_source_config

        input_names = [f.name for f in config.fields if isinstance(f, SourceFieldInputConfig)]
        assert input_names == ["realm_id", "client_id", "client_secret", "refresh_token"]

        environment = next(f for f in config.fields if isinstance(f, SourceFieldSelectConfig))
        assert environment.name == "environment"
        assert [option.value for option in environment.options] == ["production", "sandbox"]
        assert environment.defaultValue == "production"

    @pytest.mark.parametrize(
        "field_name, expected_type, expected_secret",
        [
            ("realm_id", SourceFieldInputConfigType.TEXT, False),
            ("client_id", SourceFieldInputConfigType.TEXT, False),
            ("client_secret", SourceFieldInputConfigType.PASSWORD, True),
            ("refresh_token", SourceFieldInputConfigType.PASSWORD, True),
        ],
    )
    def test_credential_fields_are_typed_and_required(
        self, field_name: str, expected_type: SourceFieldInputConfigType, expected_secret: bool
    ) -> None:
        field = next(
            f
            for f in self.source.get_source_config.fields
            if isinstance(f, SourceFieldInputConfig) and f.name == field_name
        )

        assert field.type == expected_type
        assert field.secret is expected_secret
        assert field.required is True

    def test_api_version_metadata(self) -> None:
        assert self.source.supported_versions == ("v3",)
        assert self.source.default_version == "v3"
        assert self.source.api_docs_url is not None
        assert self.source.api_docs_url.startswith("https://")

    def test_lists_tables_without_credentials(self) -> None:
        # get_schemas iterates a static entity catalog with no I/O, so public docs can render it.
        assert self.source.lists_tables_without_credentials is True

    @pytest.mark.parametrize(
        "observed_error",
        [
            "400 Client Error: Bad Request for url: https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
            "401 Client Error: Unauthorized for url: https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
            "401 Client Error: Unauthorized for url: https://quickbooks.api.intuit.com/v3/company/1/query",
            "401 Client Error: Unauthorized for url: https://sandbox-quickbooks.api.intuit.com/v3/company/1/query",
            "403 Client Error: Forbidden for url: https://quickbooks.api.intuit.com/v3/company/1/query",
            "403 Client Error: Forbidden for url: https://sandbox-quickbooks.api.intuit.com/v3/company/1/query",
        ],
    )
    def test_non_retryable_errors_match_auth_failures(self, observed_error: str) -> None:
        assert any(key in observed_error for key in self.source.get_non_retryable_errors())

    @pytest.mark.parametrize(
        "other_error",
        [
            "401 Client Error: Unauthorized for url: https://api.stripe.com/v1/customers",
            "429 Client Error: Too Many Requests for url: https://quickbooks.api.intuit.com/v3/company/1/query",
            "500 Server Error for url: https://quickbooks.api.intuit.com/v3/company/1/query",
        ],
    )
    def test_non_retryable_errors_does_not_match_unrelated(self, other_error: str) -> None:
        assert not any(key in other_error for key in self.source.get_non_retryable_errors())

    def test_get_schemas(self) -> None:
        schemas = self.source.get_schemas(self.config, self.team_id)

        assert {schema.name for schema in schemas} == set(ENDPOINTS)
        assert len(schemas) == len(ENDPOINTS)

    def test_singletons_are_full_refresh_only(self) -> None:
        schemas = {schema.name: schema for schema in self.source.get_schemas(self.config, self.team_id)}

        # One row per company, so a Metadata.LastUpdatedTime cursor buys nothing.
        for name in ("CompanyInfo", "Preferences"):
            assert schemas[name].supports_incremental is False
            assert schemas[name].incremental_fields == []

    @pytest.mark.parametrize("entity_name", [name for name, e in QUICKBOOKS_ENTITIES.items() if not e.singleton])
    def test_regular_entities_advertise_the_last_updated_cursor(self, entity_name: str) -> None:
        schema = next(s for s in self.source.get_schemas(self.config, self.team_id) if s.name == entity_name)

        assert schema.supports_incremental is True
        assert schema.incremental_fields == INCREMENTAL_FIELDS[entity_name]
        assert [f["field"] for f in schema.incremental_fields] == ["LastUpdatedTime"]

    def test_get_schemas_filtered_by_names(self) -> None:
        schemas = self.source.get_schemas(self.config, self.team_id, names=["Invoice"])

        assert [schema.name for schema in schemas] == ["Invoice"]

    def test_get_schemas_filtered_unknown_name_returns_empty(self) -> None:
        assert self.source.get_schemas(self.config, self.team_id, names=["Nope"]) == []

    def test_canonical_descriptions_cover_every_entity(self) -> None:
        descriptions = self.source.get_canonical_descriptions()

        assert set(descriptions) == set(ENDPOINTS)
        assert all(entry.get("description") for entry in descriptions.values())

    @pytest.mark.parametrize(
        "mock_return, expected_valid, expected_message",
        [
            (True, True, None),
            (False, False, "Invalid QuickBooks credentials"),
        ],
    )
    @mock.patch(f"{_SOURCE_MODULE}.validate_quickbooks_credentials")
    def test_validate_credentials(
        self,
        mock_validate: mock.MagicMock,
        mock_return: bool,
        expected_valid: bool,
        expected_message: str | None,
    ) -> None:
        mock_validate.return_value = mock_return

        is_valid, error_message = self.source.validate_credentials(self.config, self.team_id)

        assert is_valid is expected_valid
        assert error_message == expected_message
        assert mock_validate.call_args.kwargs["realm_id"] == "123456789"
        assert mock_validate.call_args.kwargs["api_version"] == "v3"

    @pytest.mark.parametrize("realm_id", ["", "   ", "abc", "123-456", "company/1"])
    @mock.patch(f"{_SOURCE_MODULE}.validate_quickbooks_credentials")
    def test_non_numeric_realm_id_is_rejected_before_any_request(
        self, mock_validate: mock.MagicMock, realm_id: str
    ) -> None:
        # The realm ID lands straight in the request path, so it never leaves the numeric shape.
        config = QuickBooksSourceConfig(
            realm_id=realm_id,
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="refresh-token",
            environment="production",
        )

        is_valid, error_message = self.source.validate_credentials(config, self.team_id)

        assert is_valid is False
        assert error_message == "QuickBooks company ID (realm ID) must be numeric"
        mock_validate.assert_not_called()

    @mock.patch(f"{_SOURCE_MODULE}.validate_quickbooks_credentials")
    def test_padded_realm_id_is_trimmed(self, mock_validate: mock.MagicMock) -> None:
        mock_validate.return_value = True
        config = QuickBooksSourceConfig(
            realm_id="  123456789  ",
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="refresh-token",
            environment="production",
        )

        assert self.source.validate_credentials(config, self.team_id) == (True, None)
        assert mock_validate.call_args.kwargs["realm_id"] == "123456789"

    def test_get_resumable_source_manager_binds_resume_config(self) -> None:
        manager = self.source.get_resumable_source_manager(mock.MagicMock())

        assert isinstance(manager, ResumableSourceManager)
        assert manager._data_class is QuickBooksResumeConfig

    @mock.patch(f"{_SOURCE_MODULE}.quickbooks_source")
    def test_source_for_pipeline_plumbs_arguments(self, mock_quickbooks_source: mock.MagicMock) -> None:
        inputs = mock.MagicMock()
        inputs.schema_name = "Invoice"
        inputs.should_use_incremental_field = True
        inputs.db_incremental_field_last_value = "2024-01-02T03:04:05Z"
        inputs.api_version = None
        manager = mock.MagicMock()

        self.source.source_for_pipeline(self.config, manager, inputs)

        kwargs = mock_quickbooks_source.call_args.kwargs
        assert kwargs["environment"] == "production"
        assert kwargs["realm_id"] == "123456789"
        assert kwargs["client_id"] == "client-id"
        assert kwargs["client_secret"] == "client-secret"
        assert kwargs["refresh_token"] == "refresh-token"
        assert kwargs["entity_name"] == "Invoice"
        # An unpinned source falls back to the source class's default version.
        assert kwargs["api_version"] == "v3"
        assert kwargs["resumable_source_manager"] is manager
        assert kwargs["should_use_incremental_field"] is True
        assert kwargs["db_incremental_field_last_value"] == "2024-01-02T03:04:05Z"

    @mock.patch(f"{_SOURCE_MODULE}.quickbooks_source")
    def test_source_for_pipeline_omits_last_value_on_full_refresh(self, mock_quickbooks_source: mock.MagicMock) -> None:
        inputs = mock.MagicMock()
        inputs.schema_name = "CompanyInfo"
        inputs.should_use_incremental_field = False
        inputs.db_incremental_field_last_value = "2024-01-02T03:04:05Z"
        inputs.api_version = None

        self.source.source_for_pipeline(self.config, mock.MagicMock(), inputs)

        assert mock_quickbooks_source.call_args.kwargs["db_incremental_field_last_value"] is None

    @mock.patch(f"{_SOURCE_MODULE}.quickbooks_source")
    def test_source_for_pipeline_honors_a_pinned_api_version(self, mock_quickbooks_source: mock.MagicMock) -> None:
        inputs = mock.MagicMock()
        inputs.schema_name = "Invoice"
        inputs.should_use_incremental_field = False
        inputs.api_version = "v3"

        self.source.source_for_pipeline(self.config, mock.MagicMock(), inputs)

        assert mock_quickbooks_source.call_args.kwargs["api_version"] == "v3"
