import datetime
from typing import Any, Optional

import pytest
from unittest import mock

from posthog.schema import ReleaseStatus, SourceFieldInputConfig, SourceFieldInputConfigType

from products.warehouse_sources.backend.temporal.data_imports.sources.common.resumable import ResumableSourceManager
from products.warehouse_sources.backend.temporal.data_imports.sources.generated_configs.xero import XeroSourceConfig
from products.warehouse_sources.backend.temporal.data_imports.sources.xero.settings import (
    ENDPOINTS,
    INCREMENTAL_FIELDS,
    XERO_ENDPOINTS,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.xero.source import XeroSource
from products.warehouse_sources.backend.temporal.data_imports.sources.xero.xero import XeroResumeConfig
from products.warehouse_sources.backend.types import ExternalDataSourceType

SOURCE_MODULE = "products.warehouse_sources.backend.temporal.data_imports.sources.xero.source"


def _inputs(schema_name: str = "invoices", **overrides: Any) -> mock.MagicMock:
    inputs = mock.MagicMock()
    inputs.schema_name = schema_name
    inputs.should_use_incremental_field = overrides.get("should_use_incremental_field", False)
    inputs.db_incremental_field_last_value = overrides.get("db_incremental_field_last_value")
    return inputs


class TestXeroSource:
    def setup_method(self) -> None:
        self.source = XeroSource()
        self.team_id = 123
        self.config = XeroSourceConfig(client_id="cid", client_secret="sec")

    def test_source_type(self) -> None:
        assert self.source.source_type == ExternalDataSourceType.XERO

    def test_get_source_config(self) -> None:
        config = self.source.get_source_config
        assert config.name.value == "Xero"
        assert config.label == "Xero"
        assert config.iconPath == "/static/services/xero.png"
        assert config.releaseStatus == ReleaseStatus.ALPHA
        assert not config.unreleasedSource

        fields = [f for f in config.fields if isinstance(f, SourceFieldInputConfig)]
        assert [f.name for f in fields] == ["client_id", "client_secret", "refresh_token", "tenant_id"]

    @pytest.mark.parametrize(
        "field_name, required, secret",
        [
            ("client_id", True, False),
            ("client_secret", True, True),
            ("refresh_token", False, True),
            ("tenant_id", False, False),
        ],
    )
    def test_credential_fields_are_shaped_for_their_sensitivity(
        self, field_name: str, required: bool, secret: bool
    ) -> None:
        config = self.source.get_source_config
        field = next(f for f in config.fields if isinstance(f, SourceFieldInputConfig) and f.name == field_name)
        assert field.required is required
        assert field.secret is secret
        assert (field.type == SourceFieldInputConfigType.PASSWORD) is secret

    def test_get_schemas_covers_every_endpoint(self) -> None:
        schemas = self.source.get_schemas(self.config, self.team_id)
        assert {s.name for s in schemas} == set(ENDPOINTS)

    @pytest.mark.parametrize("endpoint_name", sorted(ENDPOINTS))
    def test_incremental_support_matches_the_endpoint_catalog(self, endpoint_name: str) -> None:
        schema = next(s for s in self.source.get_schemas(self.config, self.team_id) if s.name == endpoint_name)
        expected_field = XERO_ENDPOINTS[endpoint_name].incremental_field

        assert schema.supports_incremental is (expected_field is not None)
        assert [f["field"] for f in schema.incremental_fields] == ([expected_field] if expected_field else [])

    def test_get_schemas_filtered_by_names(self) -> None:
        schemas = self.source.get_schemas(self.config, self.team_id, names=["invoices", "nope"])
        assert [s.name for s in schemas] == ["invoices"]

    def test_documented_tables_render_without_credentials(self) -> None:
        tables = self.source.get_documented_tables()
        assert {t["name"] for t in tables} == set(ENDPOINTS)
        assert all(t["description"] for t in tables)

    def test_canonical_descriptions_only_key_real_endpoints(self) -> None:
        descriptions = self.source.get_canonical_descriptions()
        assert set(descriptions) <= set(ENDPOINTS)
        for endpoint_name, entry in descriptions.items():
            incremental = XERO_ENDPOINTS[endpoint_name].incremental_field
            if incremental is not None:
                assert incremental in entry["columns"]

    @pytest.mark.parametrize(
        "observed_error",
        [
            "Xero rejected the credentials (401). Check the client ID and secret",
            "401 Client Error: Unauthorized for url: https://api.xero.com/api.xro/2.0/Invoices",
            "403 Client Error: Forbidden for url: https://api.xero.com/api.xro/2.0/Journals",
            "Xero organization tenant-z is not connected to this app",
        ],
    )
    def test_non_retryable_errors_match_permanent_failures(self, observed_error: str) -> None:
        assert any(key in observed_error for key in self.source.get_non_retryable_errors())

    @pytest.mark.parametrize(
        "observed_error",
        [
            "429 Client Error: Too Many Requests for url: https://api.xero.com/api.xro/2.0/Invoices",
            "503 Server Error: Service Unavailable for url: https://api.xero.com/api.xro/2.0/Invoices",
            "Read timed out",
        ],
    )
    def test_non_retryable_errors_leave_transient_failures_alone(self, observed_error: str) -> None:
        assert not any(key in observed_error for key in self.source.get_non_retryable_errors())

    @mock.patch(f"{SOURCE_MODULE}.validate_xero_credentials")
    def test_validate_credentials_passes_every_credential_field(self, mock_validate: mock.MagicMock) -> None:
        mock_validate.return_value = (True, None)
        config = XeroSourceConfig(client_id="cid", client_secret="sec", refresh_token="refresh-1", tenant_id="tenant-a")

        assert self.source.validate_credentials(config, self.team_id) == (True, None)
        assert mock_validate.call_args.kwargs == {
            "client_id": "cid",
            "client_secret": "sec",
            "refresh_token": "refresh-1",
            "tenant_id": "tenant-a",
        }

    @mock.patch(f"{SOURCE_MODULE}.validate_xero_credentials")
    def test_validate_credentials_surfaces_failure(self, mock_validate: mock.MagicMock) -> None:
        mock_validate.return_value = (False, "Xero rejected the credentials")
        assert self.source.validate_credentials(self.config, self.team_id) == (
            False,
            "Xero rejected the credentials",
        )

    def test_get_resumable_source_manager_binds_resume_config(self) -> None:
        manager = self.source.get_resumable_source_manager(mock.MagicMock())
        assert isinstance(manager, ResumableSourceManager)
        assert manager._data_class is XeroResumeConfig

    @pytest.mark.parametrize(
        "should_use_incremental_field, last_value, expected",
        [
            (True, datetime.datetime(2024, 3, 1), datetime.datetime(2024, 3, 1)),
            # A stored watermark must not leak into a full-refresh run.
            (False, datetime.datetime(2024, 3, 1), None),
        ],
    )
    @mock.patch(f"{SOURCE_MODULE}.xero_source")
    def test_source_for_pipeline_plumbs_arguments(
        self,
        mock_xero_source: mock.MagicMock,
        should_use_incremental_field: bool,
        last_value: datetime.datetime,
        expected: Optional[datetime.datetime],
    ) -> None:
        manager = mock.MagicMock()
        inputs = _inputs(
            "contacts",
            should_use_incremental_field=should_use_incremental_field,
            db_incremental_field_last_value=last_value,
        )

        self.source.source_for_pipeline(self.config, manager, inputs)

        kwargs = mock_xero_source.call_args.kwargs
        assert kwargs["client_id"] == "cid"
        assert kwargs["client_secret"] == "sec"
        assert kwargs["refresh_token"] is None
        assert kwargs["tenant_id"] is None
        assert kwargs["endpoint_name"] == "contacts"
        assert kwargs["resumable_source_manager"] is manager
        assert kwargs["db_incremental_field_last_value"] == expected


class TestXeroSettings:
    @pytest.mark.parametrize("endpoint_name", sorted(ENDPOINTS))
    def test_endpoint_catalog_is_internally_consistent(self, endpoint_name: str) -> None:
        endpoint = XERO_ENDPOINTS[endpoint_name]
        assert endpoint.name == endpoint_name
        assert endpoint.primary_key
        # A partition key must be a creation timestamp — partitioning on a mutable field
        # rewrites every partition on each sync.
        assert endpoint.partition_key in (None, "CreatedDateUTC")
        assert (endpoint_name in INCREMENTAL_FIELDS) is (endpoint.incremental_field is not None)

    def test_only_journals_uses_offset_pagination(self) -> None:
        offset_endpoints = [name for name, e in XERO_ENDPOINTS.items() if e.pagination == "offset"]
        assert offset_endpoints == ["journals"]
