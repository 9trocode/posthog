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
from products.warehouse_sources.backend.temporal.data_imports.sources.clover.clover import (
    CloverResumeConfig,
    clover_source,
    endpoint_permissions as clover_endpoint_permissions,
    validate_credentials as validate_clover_credentials,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.clover.settings import (
    DEFAULT_REGION,
    ENDPOINTS,
    INCREMENTAL_FIELDS,
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
from products.warehouse_sources.backend.temporal.data_imports.sources.generated_configs.clover import CloverSourceConfig
from products.warehouse_sources.backend.types import ExternalDataSourceType


def _credentials(config: CloverSourceConfig) -> tuple[str | None, str | None, str | None]:
    """Unpack the selected auth option into (api_token, client_id, refresh_token)."""
    auth = config.auth_type
    if auth.selection == "oauth":
        return None, auth.client_id, auth.refresh_token
    return auth.api_token, None, None


@SourceRegistry.register
class CloverSource(ResumableSource[CloverSourceConfig, CloverResumeConfig]):
    lists_tables_without_credentials = True  # static endpoint catalog — safe for public docs

    supported_versions = ("v3",)
    default_version = "v3"
    api_docs_url = "https://docs.clover.com/dev/reference/api-reference-overview"

    @property
    def source_type(self) -> ExternalDataSourceType:
        return ExternalDataSourceType.CLOVER

    @property
    def get_source_config(self) -> SourceConfig:
        return SourceConfig(
            name=SchemaExternalDataSourceType.CLOVER,
            category=DataWarehouseSourceCategory.PAYMENTS___BILLING,
            keywords=["pos", "point of sale", "fiserv"],
            label="Clover",
            releaseStatus=ReleaseStatus.ALPHA,
            caption="""Sync orders, payments, refunds, inventory and customers from a Clover merchant into the PostHog Data warehouse.

Pick the region your Clover app was created in, then enter the merchant ID you want to sync. The simplest credential is an API token generated for that merchant in the Clover dashboard. If you run a Clover app instead, choose OAuth and enter the app ID plus the refresh token from the merchant's install. PostHog mints a short-lived access token for each sync.

Your app needs the read permission for every entity you want to sync, such as orders, payments and inventory. Tables you have not been granted are flagged in the table picker.""",
            iconPath="/static/services/clover.png",
            docsUrl="https://posthog.com/docs/cdp/sources/clover",
            fields=cast(
                list[FieldType],
                [
                    SourceFieldSelectConfig(
                        name="region",
                        label="Region",
                        required=True,
                        defaultValue=DEFAULT_REGION,
                        options=[
                            SourceFieldSelectConfigOption(label="North America", value="na"),
                            SourceFieldSelectConfigOption(label="Europe", value="eu"),
                            SourceFieldSelectConfigOption(label="Latin America", value="latam"),
                            SourceFieldSelectConfigOption(label="Sandbox (developer testing)", value="sandbox"),
                        ],
                    ),
                    SourceFieldInputConfig(
                        name="merchant_id",
                        label="Merchant ID",
                        type=SourceFieldInputConfigType.TEXT,
                        required=True,
                        placeholder="",
                        secret=False,
                    ),
                    # Which sub-fields are needed depends on the selected option, so they are all
                    # optional here and the combination is checked in validate_credentials.
                    SourceFieldSelectConfig(
                        name="auth_type",
                        label="Authentication",
                        required=True,
                        defaultValue="api_token",
                        options=[
                            SourceFieldSelectConfigOption(
                                label="API token",
                                value="api_token",
                                fields=cast(
                                    list[FieldType],
                                    [
                                        SourceFieldInputConfig(
                                            name="api_token",
                                            label="API token",
                                            type=SourceFieldInputConfigType.PASSWORD,
                                            required=False,
                                            placeholder="",
                                            secret=True,
                                        ),
                                    ],
                                ),
                            ),
                            SourceFieldSelectConfigOption(
                                label="OAuth app",
                                value="oauth",
                                fields=cast(
                                    list[FieldType],
                                    [
                                        SourceFieldInputConfig(
                                            name="client_id",
                                            label="App ID",
                                            type=SourceFieldInputConfigType.TEXT,
                                            required=False,
                                            placeholder="",
                                            secret=False,
                                        ),
                                        SourceFieldInputConfig(
                                            name="refresh_token",
                                            label="Refresh token",
                                            type=SourceFieldInputConfigType.PASSWORD,
                                            required=False,
                                            placeholder="",
                                            secret=True,
                                        ),
                                    ],
                                ),
                            ),
                        ],
                    ),
                ],
            ),
        )

    def get_non_retryable_errors(self) -> dict[str, str | None]:
        return {
            "401 Client Error: Unauthorized for url": "Clover rejected your credentials. Generate a new API token, or reconnect your app, and update the source.",
            "403 Client Error: Forbidden for url": "Your Clover app is missing a read permission for this table. Grant it in the Clover dashboard and sync again.",
            "[clover_token_error]": "PostHog could not refresh your Clover access token. Check the app ID and refresh token, and that the merchant still has the app installed.",
        }

    def get_canonical_descriptions(self) -> CanonicalDescriptions:
        from products.warehouse_sources.backend.temporal.data_imports.sources.clover.canonical_descriptions import (
            CANONICAL_DESCRIPTIONS,
        )

        return CANONICAL_DESCRIPTIONS

    def get_schemas(
        self,
        config: CloverSourceConfig,
        team_id: int,
        with_counts: bool = False,
        names: list[str] | None = None,
        force_refresh: bool = False,
        api_version: str | None = None,
    ) -> list[SourceSchema]:
        # Merge only: a resumed run re-fetches the page it checkpointed on, and consecutive filter
        # windows share their boundary millisecond, so append mode would duplicate those rows.
        return build_endpoint_schemas(ENDPOINTS, INCREMENTAL_FIELDS, names, merge_only=ENDPOINTS)

    def validate_credentials(
        self,
        config: CloverSourceConfig,
        team_id: int,
        schema_name: Optional[str] = None,
        api_version: str | None = None,
    ) -> tuple[bool, str | None]:
        api_token, client_id, refresh_token = _credentials(config)
        return validate_clover_credentials(
            region=config.region,
            merchant_id=config.merchant_id,
            api_token=api_token,
            client_id=client_id,
            refresh_token=refresh_token,
            # At source-create a 403 only means the app wasn't granted that entity, which must not
            # block connecting; a per-schema check is asking about that entity specifically.
            accept_forbidden=schema_name is None,
        )

    def get_endpoint_permissions(
        self,
        config: CloverSourceConfig,
        team_id: int,
        endpoints: list[str],
        api_version: str | None = None,
    ) -> dict[str, str | None]:
        api_token, client_id, refresh_token = _credentials(config)
        return clover_endpoint_permissions(
            region=config.region,
            merchant_id=config.merchant_id,
            endpoints=endpoints,
            api_token=api_token,
            client_id=client_id,
            refresh_token=refresh_token,
        )

    def get_resumable_source_manager(self, inputs: SourceInputs) -> ResumableSourceManager[CloverResumeConfig]:
        return ResumableSourceManager[CloverResumeConfig](inputs, CloverResumeConfig)

    def source_for_pipeline(
        self,
        config: CloverSourceConfig,
        resumable_source_manager: ResumableSourceManager[CloverResumeConfig],
        inputs: SourceInputs,
    ) -> SourceResponse:
        api_token, client_id, refresh_token = _credentials(config)
        return clover_source(
            region=config.region,
            merchant_id=config.merchant_id,
            endpoint=inputs.schema_name,
            team_id=inputs.team_id,
            job_id=inputs.job_id,
            resumable_source_manager=resumable_source_manager,
            api_token=api_token,
            client_id=client_id,
            refresh_token=refresh_token,
            incremental_field=inputs.incremental_field,
            should_use_incremental_field=inputs.should_use_incremental_field,
            db_incremental_field_last_value=inputs.db_incremental_field_last_value
            if inputs.should_use_incremental_field
            else None,
        )
