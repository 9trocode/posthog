from typing import Optional, cast

from posthog.schema import (
    DataWarehouseSourceCategory,
    ExternalDataSourceType as SchemaExternalDataSourceType,
    ReleaseStatus,
    SourceConfig,
    SourceFieldInputConfig,
    SourceFieldInputConfigType,
)

from products.warehouse_sources.backend.temporal.data_imports.pipelines.pipeline.typings import (
    SourceInputs,
    SourceResponse,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.common.base import FieldType, ResumableSource
from products.warehouse_sources.backend.temporal.data_imports.sources.common.canonical_descriptions import (
    CanonicalDescriptions,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.common.registry import SourceRegistry
from products.warehouse_sources.backend.temporal.data_imports.sources.common.resumable import ResumableSourceManager
from products.warehouse_sources.backend.temporal.data_imports.sources.common.schema import (
    SourceSchema,
    build_endpoint_schemas,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.generated_configs.xero import XeroSourceConfig
from products.warehouse_sources.backend.temporal.data_imports.sources.xero.settings import ENDPOINTS, INCREMENTAL_FIELDS
from products.warehouse_sources.backend.temporal.data_imports.sources.xero.xero import (
    XeroResumeConfig,
    validate_credentials as validate_xero_credentials,
    xero_source,
)
from products.warehouse_sources.backend.types import ExternalDataSourceType


@SourceRegistry.register
class XeroSource(ResumableSource[XeroSourceConfig, XeroResumeConfig]):
    lists_tables_without_credentials = True  # static endpoint catalog — safe for public docs
    supported_versions = ("2.0",)
    default_version = "2.0"
    api_docs_url = "https://developer.xero.com/documentation/api/accounting/overview"

    @property
    def source_type(self) -> ExternalDataSourceType:
        return ExternalDataSourceType.XERO

    def get_non_retryable_errors(self) -> dict[str, str | None]:
        return {
            "Xero rejected the credentials": "Xero rejected your credentials. Check the client ID and secret — and, for an authorization-code app, that the refresh token is still valid.",
            "401 Client Error: Unauthorized for url: https://api.xero.com": "Xero rejected your access token. Reconnect the source with fresh credentials.",
            "403 Client Error: Forbidden for url: https://api.xero.com": "Your Xero app is missing a scope for this table. Grant the matching read scope and reconnect.",
            "is not connected to this app": "That Xero organization is no longer connected to your app. Reconnect it, or clear the organization ID to sync every connected organization.",
        }

    def get_canonical_descriptions(self) -> CanonicalDescriptions:
        from products.warehouse_sources.backend.temporal.data_imports.sources.xero.canonical_descriptions import (
            CANONICAL_DESCRIPTIONS,
        )

        return CANONICAL_DESCRIPTIONS

    def get_schemas(
        self,
        config: XeroSourceConfig,
        team_id: int,
        with_counts: bool = False,
        names: list[str] | None = None,
        force_refresh: bool = False,
        api_version: str | None = None,
    ) -> list[SourceSchema]:
        return build_endpoint_schemas(ENDPOINTS, INCREMENTAL_FIELDS, names)

    def validate_credentials(
        self,
        config: XeroSourceConfig,
        team_id: int,
        schema_name: Optional[str] = None,
        api_version: str | None = None,
    ) -> tuple[bool, str | None]:
        return validate_xero_credentials(
            client_id=config.client_id,
            client_secret=config.client_secret,
            refresh_token=config.refresh_token,
            tenant_id=config.tenant_id,
        )

    def get_resumable_source_manager(self, inputs: SourceInputs) -> ResumableSourceManager[XeroResumeConfig]:
        return ResumableSourceManager[XeroResumeConfig](inputs, XeroResumeConfig)

    def source_for_pipeline(
        self,
        config: XeroSourceConfig,
        resumable_source_manager: ResumableSourceManager[XeroResumeConfig],
        inputs: SourceInputs,
    ) -> SourceResponse:
        return xero_source(
            client_id=config.client_id,
            client_secret=config.client_secret,
            refresh_token=config.refresh_token,
            tenant_id=config.tenant_id,
            endpoint_name=inputs.schema_name,
            resumable_source_manager=resumable_source_manager,
            logger=inputs.logger,
            db_incremental_field_last_value=inputs.db_incremental_field_last_value
            if inputs.should_use_incremental_field
            else None,
        )

    @property
    def get_source_config(self) -> SourceConfig:
        return SourceConfig(
            name=SchemaExternalDataSourceType.XERO,
            category=DataWarehouseSourceCategory.FINANCE___ACCOUNTING,
            label="Xero",
            caption="""Sync your Xero accounting data into the PostHog Data warehouse.

The simplest setup is a Xero [custom connection](https://developer.xero.com/documentation/guides/oauth2/custom-connections), which authenticates with just a client ID and secret. If you'd rather use a standard OAuth app, add a refresh token as well. Xero rotates refresh tokens on every use, so a custom connection is the more reliable choice for a recurring sync.

Your app needs these read scopes: `accounting.transactions.read`, `accounting.contacts.read`, `accounting.settings.read` and `accounting.journals.read`.""",
            iconPath="/static/services/xero.png",
            docsUrl="https://posthog.com/docs/cdp/sources/xero",
            releaseStatus=ReleaseStatus.ALPHA,
            fields=cast(
                list[FieldType],
                [
                    SourceFieldInputConfig(
                        name="client_id",
                        label="Client ID",
                        type=SourceFieldInputConfigType.TEXT,
                        required=True,
                        placeholder="",
                        secret=False,
                    ),
                    SourceFieldInputConfig(
                        name="client_secret",
                        label="Client secret",
                        type=SourceFieldInputConfigType.PASSWORD,
                        required=True,
                        placeholder="",
                        secret=True,
                    ),
                    SourceFieldInputConfig(
                        name="refresh_token",
                        label="Refresh token (OAuth apps only)",
                        type=SourceFieldInputConfigType.PASSWORD,
                        required=False,
                        placeholder="Leave blank for a custom connection",
                        secret=True,
                    ),
                    SourceFieldInputConfig(
                        name="tenant_id",
                        label="Organization ID",
                        type=SourceFieldInputConfigType.TEXT,
                        required=False,
                        placeholder="Leave blank to sync every connected organization",
                        secret=False,
                    ),
                ],
            ),
        )
