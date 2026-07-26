import re
from typing import Optional, cast

from posthog.schema import (
    DataWarehouseSourceCategory,
    ExternalDataSourceType as SchemaExternalDataSourceType,
    ReleaseStatus,
    SourceConfig,
    SourceFieldInputConfig,
    SourceFieldInputConfigType,
    SourceFieldSelectConfig,
    SourceFieldSelectConfigOption,
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
from products.warehouse_sources.backend.temporal.data_imports.sources.generated_configs.quickbooks import (
    QuickBooksSourceConfig,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.quickbooks.quickbooks import (
    QuickBooksResumeConfig,
    quickbooks_source,
    validate_credentials as validate_quickbooks_credentials,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.quickbooks.settings import (
    ENDPOINTS,
    INCREMENTAL_FIELDS,
)
from products.warehouse_sources.backend.types import ExternalDataSourceType

# Realm IDs (company IDs) are numeric strings and land straight in the request path.
_REALM_ID_PATTERN = re.compile(r"^\d+$")


@SourceRegistry.register
class QuickBooksSource(ResumableSource[QuickBooksSourceConfig, QuickBooksResumeConfig]):
    # The Accounting API version lives in the request path (`/v3/company/{realmId}`).
    supported_versions = ("v3",)
    default_version = "v3"
    api_docs_url = "https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/account"

    lists_tables_without_credentials = True  # static entity catalog — safe for public docs

    @property
    def source_type(self) -> ExternalDataSourceType:
        return ExternalDataSourceType.QUICKBOOKS

    def get_non_retryable_errors(self) -> dict[str, str | None]:
        return {
            "400 Client Error: Bad Request for url: https://oauth.platform.intuit.com": "QuickBooks authentication failed. Your refresh token is expired or revoked, so generate a new one and reconnect.",
            "401 Client Error: Unauthorized for url: https://oauth.platform.intuit.com": "QuickBooks authentication failed. Please check your app's client ID and secret.",
            "401 Client Error: Unauthorized for url: https://quickbooks.api.intuit.com": "QuickBooks rejected the access token. Please reconnect with a fresh refresh token.",
            "401 Client Error: Unauthorized for url: https://sandbox-quickbooks.api.intuit.com": "QuickBooks rejected the access token. Please reconnect with a fresh refresh token.",
            "403 Client Error: Forbidden for url: https://quickbooks.api.intuit.com": "QuickBooks denied access to this company. Please check that your app is authorized with the accounting scope and that the company ID is right.",
            "403 Client Error: Forbidden for url: https://sandbox-quickbooks.api.intuit.com": "QuickBooks denied access to this company. Please check that your app is authorized with the accounting scope and that the company ID is right.",
        }

    @property
    def get_source_config(self) -> SourceConfig:
        return SourceConfig(
            name=SchemaExternalDataSourceType.QUICK_BOOKS,
            category=DataWarehouseSourceCategory.FINANCE___ACCOUNTING,
            keywords=["qb", "qbo", "quickbooks online", "intuit"],
            label="QuickBooks",
            caption="""Connect your QuickBooks Online company to pull invoices, payments, customers, and the rest of your accounting data into the PostHog Data warehouse.

Create an app in the Intuit developer portal with the `com.intuit.quickbooks.accounting` scope, authorize it against your company, then enter its client ID and secret along with the resulting refresh token. Your company ID (also called the realm ID) is shown in QuickBooks under Settings > Additional info.

Intuit refresh tokens expire after 100 days, so reconnect the source before then to keep syncing.""",
            iconPath="/static/services/quickbooks.png",
            docsUrl="https://posthog.com/docs/cdp/sources/quickbooks",
            releaseStatus=ReleaseStatus.ALPHA,
            fields=cast(
                list[FieldType],
                [
                    SourceFieldSelectConfig(
                        name="environment",
                        label="Environment",
                        required=True,
                        defaultValue="production",
                        options=[
                            SourceFieldSelectConfigOption(label="Production", value="production"),
                            SourceFieldSelectConfigOption(label="Sandbox", value="sandbox"),
                        ],
                    ),
                    SourceFieldInputConfig(
                        name="realm_id",
                        label="Company ID (realm ID)",
                        type=SourceFieldInputConfigType.TEXT,
                        required=True,
                        placeholder="123456789012345678",
                        secret=False,
                    ),
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
                        label="Refresh token",
                        type=SourceFieldInputConfigType.PASSWORD,
                        required=True,
                        placeholder="",
                        secret=True,
                    ),
                ],
            ),
        )

    def get_canonical_descriptions(self) -> CanonicalDescriptions:
        from products.warehouse_sources.backend.temporal.data_imports.sources.quickbooks.canonical_descriptions import (
            CANONICAL_DESCRIPTIONS,
        )

        return CANONICAL_DESCRIPTIONS

    def get_schemas(
        self,
        config: QuickBooksSourceConfig,
        team_id: int,
        with_counts: bool = False,
        names: list[str] | None = None,
        force_refresh: bool = False,
        api_version: str | None = None,
    ) -> list[SourceSchema]:
        return build_endpoint_schemas(ENDPOINTS, INCREMENTAL_FIELDS, names)

    def validate_credentials(
        self,
        config: QuickBooksSourceConfig,
        team_id: int,
        schema_name: Optional[str] = None,
        api_version: str | None = None,
    ) -> tuple[bool, str | None]:
        if not _REALM_ID_PATTERN.match(config.realm_id.strip()):
            return False, "QuickBooks company ID (realm ID) must be numeric"

        if validate_quickbooks_credentials(
            environment=config.environment,
            realm_id=config.realm_id.strip(),
            client_id=config.client_id,
            client_secret=config.client_secret,
            refresh_token=config.refresh_token,
            api_version=self.resolve_api_version(api_version),
        ):
            return True, None

        return False, "Invalid QuickBooks credentials"

    def get_resumable_source_manager(self, inputs: SourceInputs) -> ResumableSourceManager[QuickBooksResumeConfig]:
        return ResumableSourceManager[QuickBooksResumeConfig](inputs, QuickBooksResumeConfig)

    def source_for_pipeline(
        self,
        config: QuickBooksSourceConfig,
        resumable_source_manager: ResumableSourceManager[QuickBooksResumeConfig],
        inputs: SourceInputs,
    ) -> SourceResponse:
        return quickbooks_source(
            environment=config.environment,
            realm_id=config.realm_id.strip(),
            client_id=config.client_id,
            client_secret=config.client_secret,
            refresh_token=config.refresh_token,
            entity_name=inputs.schema_name,
            api_version=self.resolve_api_version(inputs.api_version),
            logger=inputs.logger,
            resumable_source_manager=resumable_source_manager,
            should_use_incremental_field=inputs.should_use_incremental_field,
            db_incremental_field_last_value=inputs.db_incremental_field_last_value
            if inputs.should_use_incremental_field
            else None,
        )
