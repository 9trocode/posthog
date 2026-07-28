from typing import Any

import pytest
from unittest import mock

from posthog.schema import ReleaseStatus, SourceFieldInputConfig, SourceFieldInputConfigType, SourceFieldSelectConfig

from products.warehouse_sources.backend.temporal.data_imports.pipelines.pipeline.typings import SourceInputs
from products.warehouse_sources.backend.temporal.data_imports.sources.common.resumable import ResumableSourceManager
from products.warehouse_sources.backend.temporal.data_imports.sources.display_video_360.display_video_360 import (
    DisplayVideo360ResumeConfig,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.display_video_360.settings import (
    DISPLAY_VIDEO_360_ENDPOINTS,
    ENDPOINTS,
    REPORT_ENDPOINTS,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.display_video_360.source import (
    DisplayVideo360Source,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.generated_configs.displayvideo360 import (
    DisplayVideo360AuthTypeConfig,
    DisplayVideo360SourceConfig,
)
from products.warehouse_sources.backend.types import ExternalDataSourceType

INCREMENTAL_ENTITY_ENDPOINTS = {"advertisers", "campaigns", "insertion_orders", "line_items"}
FULL_REFRESH_ENDPOINTS = {"partners", "creatives"}

SOURCE_MODULE = "products.warehouse_sources.backend.temporal.data_imports.sources.display_video_360.source"


def _make_config(**overrides: Any) -> DisplayVideo360SourceConfig:
    auth = overrides.pop(
        "auth_type",
        DisplayVideo360AuthTypeConfig(selection="service_account", service_account_key='{"client_email": "a"}'),
    )
    defaults: dict[str, Any] = {"partner_id": "1234", "advertiser_ids": None}
    defaults.update(overrides)
    return DisplayVideo360SourceConfig(auth_type=auth, **defaults)


def _make_inputs(**overrides: Any) -> SourceInputs:
    defaults: dict[str, Any] = {
        "schema_name": "line_items",
        "schema_id": "schema-1",
        "source_id": "source-1",
        "team_id": 7,
        "should_use_incremental_field": False,
        "db_incremental_field_last_value": None,
        "db_incremental_field_earliest_value": None,
        "incremental_field": None,
        "incremental_field_type": None,
        "job_id": "job-1",
        "logger": mock.MagicMock(),
        "reset_pipeline": False,
    }
    defaults.update(overrides)
    return SourceInputs(**defaults)


class TestDisplayVideo360Source:
    def setup_method(self) -> None:
        self.source = DisplayVideo360Source()
        self.team_id = 7
        self.config = _make_config()

    def test_source_type(self) -> None:
        assert self.source.source_type == ExternalDataSourceType.DISPLAYVIDEO360

    def test_source_config_is_released_in_alpha(self) -> None:
        config = self.source.get_source_config

        assert not config.unreleasedSource
        assert config.releaseStatus == ReleaseStatus.ALPHA
        assert config.iconPath == "/static/services/display_video_360.png"
        assert config.docsUrl == "https://posthog.com/docs/cdp/sources/display-video-360"

    def test_api_version_metadata(self) -> None:
        assert self.source.supported_versions == ("v4",)
        assert self.source.default_version == "v4"
        assert self.source.api_docs_url.startswith("https://")

    def test_lists_tables_without_credentials(self) -> None:
        # get_schemas walks a static endpoint catalog with no I/O, so the public docs can render it.
        assert self.source.lists_tables_without_credentials is True

    def test_account_scope_fields_require_credential_reentry(self) -> None:
        # Changing the partner or advertiser scope must re-require the credential (exfiltration gate).
        assert self.source.connection_host_fields == ["partner_id", "advertiser_ids"]

    def test_source_config_fields(self) -> None:
        auth_field, partner_field, advertiser_field = self.source.get_source_config.fields

        assert isinstance(auth_field, SourceFieldSelectConfig)
        assert auth_field.name == "auth_type"
        assert auth_field.required is True
        assert auth_field.defaultValue == "service_account"
        assert {option.value for option in auth_field.options} == {"service_account", "oauth"}

        service_account_option = next(o for o in auth_field.options if o.value == "service_account")
        assert [f.name for f in service_account_option.fields or []] == ["service_account_key"]
        oauth_option = next(o for o in auth_field.options if o.value == "oauth")
        assert [f.name for f in oauth_option.fields or []] == ["client_id", "client_secret", "refresh_token"]

        assert isinstance(partner_field, SourceFieldInputConfig)
        assert partner_field.name == "partner_id"
        assert partner_field.required is True

        assert isinstance(advertiser_field, SourceFieldInputConfig)
        assert advertiser_field.name == "advertiser_ids"
        assert advertiser_field.required is False

    def test_every_secret_field_is_a_password_or_textarea(self) -> None:
        auth_field = self.source.get_source_config.fields[0]
        assert isinstance(auth_field, SourceFieldSelectConfig)

        secret_fields = [
            field
            for option in auth_field.options
            for field in option.fields or []
            if isinstance(field, SourceFieldInputConfig) and field.secret
        ]

        assert {field.name for field in secret_fields} == {"service_account_key", "client_secret", "refresh_token"}
        for field in secret_fields:
            assert field.type in (SourceFieldInputConfigType.PASSWORD, SourceFieldInputConfigType.TEXTAREA)

    @pytest.mark.parametrize(
        "expected_key",
        ["401 Client Error", "403 Client Error", "invalid_grant", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"],
    )
    def test_non_retryable_errors(self, expected_key: str) -> None:
        assert expected_key in self.source.get_non_retryable_errors()

    def test_get_schemas_returns_every_endpoint(self) -> None:
        schemas = self.source.get_schemas(self.config, self.team_id)
        assert {schema.name for schema in schemas} == set(ENDPOINTS)

    @pytest.mark.parametrize("endpoint", sorted(INCREMENTAL_ENTITY_ENDPOINTS))
    def test_entity_endpoints_are_incremental_on_update_time(self, endpoint: str) -> None:
        schema = next(s for s in self.source.get_schemas(self.config, self.team_id) if s.name == endpoint)

        assert schema.supports_incremental is True
        assert schema.supports_append is True
        assert schema.incremental_fields == [
            {"label": "updateTime", "type": "datetime", "field": "updateTime", "field_type": "datetime"}
        ]

    @pytest.mark.parametrize("endpoint", sorted(FULL_REFRESH_ENDPOINTS))
    def test_full_refresh_endpoints_advertise_no_cursor(self, endpoint: str) -> None:
        schema = next(s for s in self.source.get_schemas(self.config, self.team_id) if s.name == endpoint)

        assert schema.supports_incremental is False
        assert schema.supports_append is False
        assert schema.incremental_fields == []

    @pytest.mark.parametrize("endpoint", sorted(REPORT_ENDPOINTS))
    def test_report_endpoints_are_incremental_on_date(self, endpoint: str) -> None:
        schema = next(s for s in self.source.get_schemas(self.config, self.team_id) if s.name == endpoint)

        assert schema.supports_incremental is True
        assert schema.incremental_fields == [{"label": "date", "type": "date", "field": "date", "field_type": "date"}]
        assert schema.description is not None

    def test_get_schemas_filtered_by_names(self) -> None:
        schemas = self.source.get_schemas(self.config, self.team_id, names=["line_items", "nonexistent"])
        assert [schema.name for schema in schemas] == ["line_items"]

    def test_canonical_descriptions_cover_every_endpoint(self) -> None:
        descriptions = self.source.get_canonical_descriptions()
        assert set(descriptions) == set(ENDPOINTS)

    def test_canonical_descriptions_document_the_primary_keys(self) -> None:
        descriptions = self.source.get_canonical_descriptions()

        for name, endpoint in DISPLAY_VIDEO_360_ENDPOINTS.items():
            columns = descriptions[name]["columns"]
            missing = [key for key in endpoint.primary_key if key not in columns]
            assert missing == [], f"{name} is missing descriptions for {missing}"

    @pytest.mark.parametrize(
        ("probe_result", "expected"),
        [((True, None), (True, None)), ((False, "nope"), (False, "nope"))],
    )
    def test_validate_credentials_delegates_to_the_transport(
        self, probe_result: tuple[bool, str | None], expected: tuple[bool, str | None]
    ) -> None:
        with mock.patch(f"{SOURCE_MODULE}.validate_display_video_360_credentials", return_value=probe_result) as probe:
            assert self.source.validate_credentials(self.config, self.team_id) == expected

        probe.assert_called_once_with(self.config, "v4")

    def test_validate_credentials_honors_a_pinned_api_version(self) -> None:
        with mock.patch(f"{SOURCE_MODULE}.validate_display_video_360_credentials", return_value=(True, None)) as probe:
            self.source.validate_credentials(self.config, self.team_id, api_version="v3")

        probe.assert_called_once_with(self.config, "v3")

    def test_resume_manager_is_namespaced_per_schema(self) -> None:
        # Entity page tokens and report windows are not interchangeable, so a retry that switches
        # schema must not load the other schema's cursor.
        manager = self.source.get_resumable_source_manager(_make_inputs(schema_name="campaigns"))

        assert isinstance(manager, ResumableSourceManager)
        assert manager._data_class is DisplayVideo360ResumeConfig
        assert manager._namespace == "campaigns"

    def test_source_for_pipeline_plumbs_arguments(self) -> None:
        inputs = _make_inputs(schema_name="campaigns")
        manager = mock.MagicMock(spec=ResumableSourceManager)

        with mock.patch(f"{SOURCE_MODULE}.display_video_360_source") as build_source:
            self.source.source_for_pipeline(self.config, manager, inputs)

        build_source.assert_called_once_with(
            config=self.config,
            endpoint="campaigns",
            api_version="v4",
            logger=inputs.logger,
            resumable_source_manager=manager,
            should_use_incremental_field=False,
            db_incremental_field_last_value=None,
        )

    def test_source_for_pipeline_drops_the_cursor_when_incremental_is_off(self) -> None:
        inputs = _make_inputs(should_use_incremental_field=False, db_incremental_field_last_value="2026-01-01")
        manager = mock.MagicMock(spec=ResumableSourceManager)

        with mock.patch(f"{SOURCE_MODULE}.display_video_360_source") as build_source:
            self.source.source_for_pipeline(self.config, manager, inputs)

        assert build_source.call_args.kwargs["db_incremental_field_last_value"] is None

    def test_source_for_pipeline_passes_the_cursor_when_incremental_is_on(self) -> None:
        inputs = _make_inputs(
            should_use_incremental_field=True,
            db_incremental_field_last_value="2026-01-01T00:00:00Z",
            incremental_field="updateTime",
            api_version="v4",
        )
        manager = mock.MagicMock(spec=ResumableSourceManager)

        with mock.patch(f"{SOURCE_MODULE}.display_video_360_source") as build_source:
            self.source.source_for_pipeline(self.config, manager, inputs)

        kwargs = build_source.call_args.kwargs
        assert kwargs["should_use_incremental_field"] is True
        assert kwargs["db_incremental_field_last_value"] == "2026-01-01T00:00:00Z"
