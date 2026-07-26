import pytest
from unittest import mock

from posthog.schema import ReleaseStatus, SourceFieldInputConfig, SourceFieldInputConfigType

from products.warehouse_sources.backend.temporal.data_imports.sources.common.resumable import ResumableSourceManager
from products.warehouse_sources.backend.temporal.data_imports.sources.dropbox.canonical_descriptions import (
    CANONICAL_DESCRIPTIONS,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.dropbox.dropbox import DropboxResumeConfig
from products.warehouse_sources.backend.temporal.data_imports.sources.dropbox.settings import (
    ENDPOINTS,
    INCREMENTAL_FIELDS,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.dropbox.source import DropboxSource
from products.warehouse_sources.backend.temporal.data_imports.sources.generated_configs.dropbox import (
    DropboxSourceConfig,
)
from products.warehouse_sources.backend.types import ExternalDataSourceType

_SOURCE_MODULE = "products.warehouse_sources.backend.temporal.data_imports.sources.dropbox.source"


class TestDropboxSource:
    def setup_method(self) -> None:
        self.source = DropboxSource()
        self.team_id = 123
        self.config = DropboxSourceConfig(
            app_key="key",
            app_secret="secret",
            refresh_token="refresh",
            folder_path="/Reports",
            team_member_id="dbmid:1",
        )

    def test_source_type(self) -> None:
        assert self.source.source_type == ExternalDataSourceType.DROPBOX

    def test_get_source_config(self) -> None:
        config = self.source.get_source_config

        assert config.name.value == "Dropbox"
        assert config.label == "Dropbox"
        assert config.releaseStatus == ReleaseStatus.ALPHA
        assert config.unreleasedSource is None
        assert config.iconPath == "/static/services/dropbox.png"

        field_names = [f.name for f in config.fields]
        assert field_names == [
            "app_key",
            "app_secret",
            "refresh_token",
            "folder_path",
            "team_member_id",
            "root_namespace_id",
        ]

    @pytest.mark.parametrize("field_name", ["app_secret", "refresh_token"])
    def test_secret_fields_are_required_passwords(self, field_name: str) -> None:
        config = self.source.get_source_config
        field = next(f for f in config.fields if isinstance(f, SourceFieldInputConfig) and f.name == field_name)

        assert field.type == SourceFieldInputConfigType.PASSWORD
        assert field.secret is True
        assert field.required is True

    @pytest.mark.parametrize("field_name", ["folder_path", "team_member_id", "root_namespace_id"])
    def test_business_and_scoping_fields_are_optional(self, field_name: str) -> None:
        config = self.source.get_source_config
        field = next(f for f in config.fields if isinstance(f, SourceFieldInputConfig) and f.name == field_name)

        assert field.required is False
        assert field.secret is False

    @pytest.mark.parametrize(
        "observed_error",
        [
            "400 Client Error: Bad Request for url: https://api.dropboxapi.com/oauth2/token",
            "401 Client Error: Unauthorized for url: https://api.dropboxapi.com/2/files/list_folder",
            "403 Client Error: Forbidden for url: https://api.dropboxapi.com/2/team_log/get_events",
            "409 Client Error: Conflict for url: https://api.dropboxapi.com/2/files/list_folder",
        ],
    )
    def test_non_retryable_errors_match_permanent_failures(self, observed_error: str) -> None:
        assert any(key in observed_error for key in self.source.get_non_retryable_errors())

    @pytest.mark.parametrize(
        "other_error",
        [
            "429 Client Error: Too Many Requests for url: https://api.dropboxapi.com/2/files/list_folder",
            "500 Server Error for url: https://api.dropboxapi.com/2/files/list_folder",
            "401 Client Error: Unauthorized for url: https://api.stripe.com/v1/customers",
        ],
    )
    def test_non_retryable_errors_leave_transient_failures_alone(self, other_error: str) -> None:
        assert not any(key in other_error for key in self.source.get_non_retryable_errors())

    def test_get_schemas_covers_every_endpoint(self) -> None:
        schemas = self.source.get_schemas(self.config, self.team_id)

        assert {schema.name for schema in schemas} == set(ENDPOINTS)

    def test_only_the_audit_log_is_incremental(self) -> None:
        schemas = {schema.name: schema for schema in self.source.get_schemas(self.config, self.team_id)}

        # `team_log/get_events` is the one endpoint with a server-side time filter.
        assert {name for name, schema in schemas.items() if schema.supports_incremental} == {"team_events"}
        assert schemas["team_events"].incremental_fields == INCREMENTAL_FIELDS["team_events"]
        assert [f["field"] for f in schemas["team_events"].incremental_fields] == ["timestamp"]
        assert schemas["files"].incremental_fields == []
        assert schemas["files"].supports_append is False

    @pytest.mark.parametrize(
        "endpoint, should_sync_default",
        [
            ("files", True),
            ("shared_links", True),
            ("shared_folders", True),
            ("team_members", False),
            ("team_events", False),
        ],
    )
    def test_team_tables_are_not_enabled_by_default(self, endpoint: str, should_sync_default: bool) -> None:
        schemas = {schema.name: schema for schema in self.source.get_schemas(self.config, self.team_id)}

        assert schemas[endpoint].should_sync_default is should_sync_default

    def test_get_schemas_filtered_by_names(self) -> None:
        schemas = self.source.get_schemas(self.config, self.team_id, names=["files"])

        assert [schema.name for schema in schemas] == ["files"]

    def test_get_schemas_filtered_unknown_name_returns_empty(self) -> None:
        assert self.source.get_schemas(self.config, self.team_id, names=["nope"]) == []

    def test_documented_tables_are_published_without_credentials(self) -> None:
        tables = self.source.get_documented_tables()

        assert {table["name"] for table in tables} == set(ENDPOINTS)
        assert all(table["description"] for table in tables)

    def test_canonical_descriptions_key_off_endpoint_names(self) -> None:
        assert set(CANONICAL_DESCRIPTIONS) <= set(ENDPOINTS)

    @pytest.mark.parametrize(
        "transport_result, expected",
        [
            ((True, None), (True, None)),
            ((False, "Could not authenticate with Dropbox."), (False, "Could not authenticate with Dropbox.")),
        ],
    )
    @mock.patch(f"{_SOURCE_MODULE}.validate_dropbox_credentials")
    def test_validate_credentials(
        self,
        mock_validate: mock.MagicMock,
        transport_result: tuple[bool, str | None],
        expected: tuple[bool, str | None],
    ) -> None:
        mock_validate.return_value = transport_result

        assert self.source.validate_credentials(self.config, self.team_id) == expected

        credentials = mock_validate.call_args.args[0]
        assert (credentials.app_key, credentials.app_secret, credentials.refresh_token) == ("key", "secret", "refresh")
        assert credentials.team_member_id == "dbmid:1"

    @mock.patch(f"{_SOURCE_MODULE}.check_endpoint_access")
    def test_get_endpoint_permissions_delegates_to_the_probe(self, mock_check: mock.MagicMock) -> None:
        mock_check.return_value = {"files": None, "team_events": "missing scope"}

        results = self.source.get_endpoint_permissions(self.config, self.team_id, ["files", "team_events"])

        assert results == {"files": None, "team_events": "missing scope"}
        assert mock_check.call_args.args[1] == ["files", "team_events"]

    def test_get_resumable_source_manager_binds_resume_config(self) -> None:
        manager = self.source.get_resumable_source_manager(mock.MagicMock())

        assert isinstance(manager, ResumableSourceManager)
        assert manager._data_class is DropboxResumeConfig

    @mock.patch(f"{_SOURCE_MODULE}.dropbox_source")
    def test_source_for_pipeline_plumbs_arguments(self, mock_dropbox_source: mock.MagicMock) -> None:
        inputs = mock.MagicMock()
        inputs.schema_name = "team_events"
        inputs.should_use_incremental_field = True
        inputs.db_incremental_field_last_value = "2024-05-01T00:00:00Z"
        manager = mock.MagicMock()

        self.source.source_for_pipeline(self.config, manager, inputs)

        kwargs = mock_dropbox_source.call_args.kwargs
        assert kwargs["endpoint"] == "team_events"
        assert kwargs["folder_path"] == "/Reports"
        assert kwargs["resumable_source_manager"] is manager
        assert kwargs["should_use_incremental_field"] is True
        assert kwargs["db_incremental_field_last_value"] == "2024-05-01T00:00:00Z"
        assert kwargs["credentials"].refresh_token == "refresh"

    @mock.patch(f"{_SOURCE_MODULE}.dropbox_source")
    def test_source_for_pipeline_omits_last_value_on_full_refresh(self, mock_dropbox_source: mock.MagicMock) -> None:
        inputs = mock.MagicMock()
        inputs.schema_name = "files"
        inputs.should_use_incremental_field = False
        inputs.db_incremental_field_last_value = "2024-05-01T00:00:00Z"

        self.source.source_for_pipeline(self.config, mock.MagicMock(), inputs)

        assert mock_dropbox_source.call_args.kwargs["db_incremental_field_last_value"] is None
