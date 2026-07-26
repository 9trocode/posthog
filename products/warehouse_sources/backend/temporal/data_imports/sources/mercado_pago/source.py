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
from products.warehouse_sources.backend.temporal.data_imports.sources.generated_configs.mercadopago import (
    MercadoPagoSourceConfig,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.mercado_pago.mercado_pago import (
    MercadoPagoCredentials,
    MercadoPagoResumeConfig,
    mercado_pago_source,
    validate_credentials as validate_mercado_pago_credentials,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.mercado_pago.settings import (
    ENDPOINTS,
    INCREMENTAL_FIELDS,
)
from products.warehouse_sources.backend.types import ExternalDataSourceType

MISSING_ACCESS_TOKEN_ERROR = "Missing Mercado Pago access token"
MISSING_OAUTH_CREDENTIALS_ERROR = "Missing Mercado Pago client ID, client secret, or refresh token"


@SourceRegistry.register
class MercadoPagoSource(ResumableSource[MercadoPagoSourceConfig, MercadoPagoResumeConfig]):
    lists_tables_without_credentials = True  # static endpoint catalog — safe for public docs

    # No pinnable API version: `/v1/payments` sits alongside unversioned `/preapproval` and
    # `/merchant_orders`, and neither is a documented version choice.
    api_docs_url = "https://www.mercadopago.com.br/developers/en/reference"

    @property
    def source_type(self) -> ExternalDataSourceType:
        return ExternalDataSourceType.MERCADOPAGO

    @property
    def get_source_config(self) -> SourceConfig:
        return SourceConfig(
            name=SchemaExternalDataSourceType.MERCADO_PAGO,
            category=DataWarehouseSourceCategory.PAYMENTS___BILLING,
            label="Mercado Pago (Mercado Libre)",
            releaseStatus=ReleaseStatus.ALPHA,
            keywords=["mercadopago", "mercado libre", "pix", "boleto"],
            caption="""Sync your Mercado Pago payments, merchant orders, and subscriptions into the PostHog Data warehouse.

For a single account, copy the production access token from **Your integrations > your application > Production credentials** in the [Mercado Pago developer panel](https://www.mercadopago.com/developers/panel).

Marketplace and multi-seller integrations should use their OAuth application instead: enter the app's client ID and secret plus a refresh token for the seller account, and PostHog mints a short-lived access token for each sync.

Payments search only covers the last 12 months, so older payments can't be backfilled.""",
            iconPath="/static/services/mercado_pago.png",
            docsUrl="https://posthog.com/docs/cdp/sources/mercado-pago",
            fields=cast(
                list[FieldType],
                [
                    SourceFieldSelectConfig(
                        name="auth_method",
                        label="Authentication type",
                        required=True,
                        defaultValue="access_token",
                        options=[
                            SourceFieldSelectConfigOption(
                                label="Access token",
                                value="access_token",
                                fields=cast(
                                    list[FieldType],
                                    [
                                        SourceFieldInputConfig(
                                            name="access_token",
                                            label="Access token",
                                            type=SourceFieldInputConfigType.PASSWORD,
                                            required=False,
                                            placeholder="APP_USR-...",
                                            secret=True,
                                        ),
                                    ],
                                ),
                            ),
                            SourceFieldSelectConfigOption(
                                label="OAuth application (marketplace)",
                                value="oauth",
                                fields=cast(
                                    list[FieldType],
                                    [
                                        SourceFieldInputConfig(
                                            name="client_id",
                                            label="Client ID",
                                            type=SourceFieldInputConfigType.TEXT,
                                            required=False,
                                            placeholder="",
                                            secret=False,
                                        ),
                                        SourceFieldInputConfig(
                                            name="client_secret",
                                            label="Client secret",
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
                                            placeholder="TG-...",
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

    def get_canonical_descriptions(self) -> CanonicalDescriptions:
        from products.warehouse_sources.backend.temporal.data_imports.sources.mercado_pago.canonical_descriptions import (
            CANONICAL_DESCRIPTIONS,
        )

        return CANONICAL_DESCRIPTIONS

    def get_non_retryable_errors(self) -> dict[str, str | None]:
        return {
            "401 Client Error: Unauthorized for url: https://api.mercadopago.com": "Your Mercado Pago credentials are invalid or expired. Generate new credentials in the developer panel and reconnect.",
            "403 Client Error: Forbidden for url: https://api.mercadopago.com": "Your Mercado Pago credentials are not authorized to read this data. Check the application's permissions and reconnect.",
            # Deterministic config errors — retrying can't fix a credential that was never entered.
            MISSING_ACCESS_TOKEN_ERROR: "No Mercado Pago access token is configured. Please update the source configuration.",
            MISSING_OAUTH_CREDENTIALS_ERROR: "The Mercado Pago client ID, client secret, or refresh token is missing. Please update the source configuration.",
        }

    def _get_credentials(self, config: MercadoPagoSourceConfig) -> MercadoPagoCredentials:
        if config.auth_method.selection == "oauth":
            if not (
                config.auth_method.client_id and config.auth_method.client_secret and config.auth_method.refresh_token
            ):
                raise ValueError(MISSING_OAUTH_CREDENTIALS_ERROR)
            return MercadoPagoCredentials(
                client_id=config.auth_method.client_id,
                client_secret=config.auth_method.client_secret,
                refresh_token=config.auth_method.refresh_token,
            )

        if not config.auth_method.access_token:
            raise ValueError(MISSING_ACCESS_TOKEN_ERROR)
        return MercadoPagoCredentials(access_token=config.auth_method.access_token)

    def get_schemas(
        self,
        config: MercadoPagoSourceConfig,
        team_id: int,
        with_counts: bool = False,
        names: list[str] | None = None,
        force_refresh: bool = False,
        api_version: str | None = None,
    ) -> list[SourceSchema]:
        return build_endpoint_schemas(ENDPOINTS, INCREMENTAL_FIELDS, names)

    def validate_credentials(
        self,
        config: MercadoPagoSourceConfig,
        team_id: int,
        schema_name: Optional[str] = None,
        api_version: str | None = None,
    ) -> tuple[bool, str | None]:
        try:
            credentials = self._get_credentials(config)
        except ValueError as e:
            return False, str(e)

        return validate_mercado_pago_credentials(credentials, schema_name)

    def get_resumable_source_manager(self, inputs: SourceInputs) -> ResumableSourceManager[MercadoPagoResumeConfig]:
        return ResumableSourceManager[MercadoPagoResumeConfig](inputs, MercadoPagoResumeConfig)

    def source_for_pipeline(
        self,
        config: MercadoPagoSourceConfig,
        resumable_source_manager: ResumableSourceManager[MercadoPagoResumeConfig],
        inputs: SourceInputs,
    ) -> SourceResponse:
        if inputs.schema_name not in ENDPOINTS:
            raise ValueError(f"Unknown Mercado Pago schema '{inputs.schema_name}'")

        return mercado_pago_source(
            credentials=self._get_credentials(config),
            endpoint=inputs.schema_name,
            team_id=inputs.team_id,
            job_id=inputs.job_id,
            resumable_source_manager=resumable_source_manager,
            should_use_incremental_field=inputs.should_use_incremental_field,
            db_incremental_field_last_value=inputs.db_incremental_field_last_value
            if inputs.should_use_incremental_field
            else None,
            incremental_field=inputs.incremental_field,
        )
