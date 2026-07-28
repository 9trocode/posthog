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
from products.warehouse_sources.backend.temporal.data_imports.sources.common.schema import SourceSchema
from products.warehouse_sources.backend.temporal.data_imports.sources.display_video_360.display_video_360 import (
    DisplayVideo360ResumeConfig,
    display_video_360_source,
    validate_credentials as validate_display_video_360_credentials,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.display_video_360.settings import (
    DISPLAY_VIDEO_360_ENDPOINTS,
    DISPLAY_VIDEO_API_VERSION,
    ENDPOINTS,
    INCREMENTAL_FIELDS,
    REPORT_ENDPOINTS,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.generated_configs.displayvideo360 import (
    DisplayVideo360SourceConfig,
)
from products.warehouse_sources.backend.types import ExternalDataSourceType


@SourceRegistry.register
class DisplayVideo360Source(ResumableSource[DisplayVideo360SourceConfig, DisplayVideo360ResumeConfig]):
    supported_versions = (DISPLAY_VIDEO_API_VERSION,)
    default_version = DISPLAY_VIDEO_API_VERSION
    api_docs_url = "https://developers.google.com/display-video/api/reference/rest"

    lists_tables_without_credentials = True  # static endpoint catalog — safe for public docs

    @property
    def source_type(self) -> ExternalDataSourceType:
        return ExternalDataSourceType.DISPLAYVIDEO360

    @property
    def get_source_config(self) -> SourceConfig:
        return SourceConfig(
            name=SchemaExternalDataSourceType.DISPLAY_VIDEO360,
            category=DataWarehouseSourceCategory.ADVERTISING,
            keywords=["dv360", "doubleclick bid manager", "google display video"],
            label="Display & Video 360",
            caption="""Connect Display & Video 360 to sync your partners, advertisers, campaigns, insertion orders, line items, creatives, and daily performance reports into the PostHog Data warehouse.

Enable both the **Display & Video 360 API** and the **Bid Manager API** on a Google Cloud project, then pick how PostHog should authenticate:

- **Service account key** — paste the JSON key file. The service account's email also has to be added as a Display & Video 360 user with access to the partner; enabling the APIs on its own isn't enough.
- **OAuth client** — create an OAuth client in the same project, authorize it for the `display-video` and `doubleclickbidmanager` scopes, then paste the client ID, client secret, and refresh token.

Performance tables are generated as Bid Manager reports, so they only reach as far back as Display & Video 360 retains reporting data.""",
            iconPath="/static/services/display_video_360.png",
            docsUrl="https://posthog.com/docs/cdp/sources/display-video-360",
            releaseStatus=ReleaseStatus.ALPHA,
            fields=cast(
                list[FieldType],
                [
                    SourceFieldSelectConfig(
                        name="auth_type",
                        label="Authentication",
                        required=True,
                        defaultValue="service_account",
                        options=[
                            SourceFieldSelectConfigOption(
                                label="Service account key",
                                value="service_account",
                                fields=cast(
                                    list[FieldType],
                                    [
                                        SourceFieldInputConfig(
                                            name="service_account_key",
                                            label="Service account JSON key",
                                            type=SourceFieldInputConfigType.TEXTAREA,
                                            required=False,
                                            placeholder='{"type": "service_account", ...}',
                                            secret=True,
                                        ),
                                    ],
                                ),
                            ),
                            SourceFieldSelectConfigOption(
                                label="OAuth client",
                                value="oauth",
                                fields=cast(
                                    list[FieldType],
                                    [
                                        SourceFieldInputConfig(
                                            name="client_id",
                                            label="OAuth client ID",
                                            type=SourceFieldInputConfigType.TEXT,
                                            required=False,
                                            placeholder="000000000000-xxxx.apps.googleusercontent.com",
                                            secret=False,
                                        ),
                                        SourceFieldInputConfig(
                                            name="client_secret",
                                            label="OAuth client secret",
                                            type=SourceFieldInputConfigType.PASSWORD,
                                            required=False,
                                            placeholder="",
                                            secret=True,
                                        ),
                                        SourceFieldInputConfig(
                                            name="refresh_token",
                                            label="Refresh token",
                                            type=SourceFieldInputConfigType.PASSWORD,
                                            required=False,
                                            placeholder="1//...",
                                            secret=True,
                                        ),
                                    ],
                                ),
                            ),
                        ],
                    ),
                    SourceFieldInputConfig(
                        name="partner_id",
                        label="Partner ID",
                        type=SourceFieldInputConfigType.TEXT,
                        required=True,
                        placeholder="123456",
                        secret=False,
                    ),
                    SourceFieldInputConfig(
                        name="advertiser_ids",
                        label="Advertiser IDs",
                        type=SourceFieldInputConfigType.TEXT,
                        required=False,
                        placeholder="Leave blank to sync every advertiser under the partner",
                        secret=False,
                    ),
                ],
            ),
        )

    def get_non_retryable_errors(self) -> dict[str, str | None]:
        return {
            "401 Client Error": "Google rejected your Display & Video 360 credentials. Update the service account key or OAuth client details and reconnect.",
            "403 Client Error": "Your credentials cannot read Display & Video 360. Add the service account or user as a Display & Video 360 user with partner access, and enable both the Display & Video 360 API and the Bid Manager API.",
            # google-auth raises this while refreshing when a refresh token has been revoked or a
            # service account key has been rotated away. No retry can recover it.
            "invalid_grant": "Your Display & Video 360 connection has expired or been revoked. Enter new credentials and reconnect.",
            "ACCESS_TOKEN_SCOPE_INSUFFICIENT": "The credentials are missing the Display & Video 360 or Bid Manager scope. Re-authorize with both scopes and reconnect.",
            "The service account key": "The service account JSON key could not be read. Paste the complete key file and reconnect.",
            "Missing OAuth credentials": "The OAuth client ID, client secret, and refresh token are all required. Add the missing values and reconnect.",
        }

    def get_canonical_descriptions(self) -> CanonicalDescriptions:
        from products.warehouse_sources.backend.temporal.data_imports.sources.display_video_360.canonical_descriptions import (
            CANONICAL_DESCRIPTIONS,
        )

        return CANONICAL_DESCRIPTIONS

    def get_schemas(
        self,
        config: DisplayVideo360SourceConfig,
        team_id: int,
        with_counts: bool = False,
        names: list[str] | None = None,
        force_refresh: bool = False,
        api_version: str | None = None,
    ) -> list[SourceSchema]:
        schemas = [
            SourceSchema(
                name=endpoint,
                supports_incremental=DISPLAY_VIDEO_360_ENDPOINTS[endpoint].supports_incremental,
                supports_append=DISPLAY_VIDEO_360_ENDPOINTS[endpoint].supports_incremental,
                incremental_fields=INCREMENTAL_FIELDS.get(endpoint, []),
                description=(
                    "Generated as a Bid Manager report, so history only reaches as far back as your "
                    "Display & Video 360 reporting retention"
                    if endpoint in REPORT_ENDPOINTS
                    else None
                ),
            )
            for endpoint in ENDPOINTS
        ]

        if names is not None:
            names_set = set(names)
            schemas = [schema for schema in schemas if schema.name in names_set]

        return schemas

    def validate_credentials(
        self,
        config: DisplayVideo360SourceConfig,
        team_id: int,
        schema_name: Optional[str] = None,
        api_version: str | None = None,
    ) -> tuple[bool, str | None]:
        return validate_display_video_360_credentials(config, self.resolve_api_version(api_version))

    def get_resumable_source_manager(self, inputs: SourceInputs) -> ResumableSourceManager[DisplayVideo360ResumeConfig]:
        # Entity page tokens and report date windows share one dataclass but are never
        # interchangeable, so keep each schema's resume state in its own namespace.
        return ResumableSourceManager[DisplayVideo360ResumeConfig](
            inputs, DisplayVideo360ResumeConfig, namespace=inputs.schema_name
        )

    def source_for_pipeline(
        self,
        config: DisplayVideo360SourceConfig,
        resumable_source_manager: ResumableSourceManager[DisplayVideo360ResumeConfig],
        inputs: SourceInputs,
    ) -> SourceResponse:
        return display_video_360_source(
            config=config,
            endpoint=inputs.schema_name,
            api_version=self.resolve_api_version(inputs.api_version),
            logger=inputs.logger,
            resumable_source_manager=resumable_source_manager,
            should_use_incremental_field=inputs.should_use_incremental_field,
            db_incremental_field_last_value=inputs.db_incremental_field_last_value
            if inputs.should_use_incremental_field
            else None,
        )
