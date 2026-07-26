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
from products.warehouse_sources.backend.temporal.data_imports.sources.generated_configs.googledrive import (
    GoogleDriveSourceConfig,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.google_drive.google_drive import (
    GOOGLE_DRIVE_API_VERSION_V3,
    MISSING_OAUTH_CREDENTIALS_ERROR,
    MISSING_SERVICE_ACCOUNT_KEY_ERROR,
    GoogleDriveAuth,
    GoogleDriveResumeConfig,
    google_drive_source,
    validate_credentials as validate_google_drive_credentials,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.google_drive.settings import (
    ENDPOINT_DESCRIPTIONS,
    GOOGLE_DRIVE_ENDPOINTS,
)
from products.warehouse_sources.backend.types import ExternalDataSourceType


@SourceRegistry.register
class GoogleDriveSource(ResumableSource[GoogleDriveSourceConfig, GoogleDriveResumeConfig]):
    api_docs_url = "https://developers.google.com/workspace/drive/api/reference/rest/v3"
    # Drive's version is a path segment (`/drive/v3/`) and v2 is retired, so v3 is the only pin.
    supported_versions = (GOOGLE_DRIVE_API_VERSION_V3,)
    default_version = GOOGLE_DRIVE_API_VERSION_V3

    lists_tables_without_credentials = True  # static endpoint catalog — safe for public docs

    @property
    def source_type(self) -> ExternalDataSourceType:
        return ExternalDataSourceType.GOOGLEDRIVE

    @property
    def get_source_config(self) -> SourceConfig:
        return SourceConfig(
            name=SchemaExternalDataSourceType.GOOGLE_DRIVE,
            category=DataWarehouseSourceCategory.FILE_STORAGE,
            label="Google Drive",
            releaseStatus=ReleaseStatus.ALPHA,
            keywords=["gdrive", "google workspace"],
            caption="""Sync your Google Drive file inventory, shared drives, and shared drive access grants. This reads file metadata only — it does not download file contents.

Whichever method you pick needs the `https://www.googleapis.com/auth/drive.readonly` scope, and the Google Drive API must be enabled in the Google Cloud project that owns the credentials.""",
            iconPath="/static/services/google_drive.png",
            docsUrl="https://posthog.com/docs/cdp/sources/google-drive",
            fields=cast(
                list[FieldType],
                [
                    SourceFieldSelectConfig(
                        name="auth_method",
                        label="Authentication type",
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
                                            caption="Paste the whole JSON key file. The service account only sees files shared with its email address, unless you also fill in the field below.",
                                            secret=True,
                                        ),
                                        SourceFieldInputConfig(
                                            name="impersonated_user_email",
                                            label="Impersonated user email",
                                            type=SourceFieldInputConfigType.EMAIL,
                                            required=False,
                                            placeholder="you@yourcompany.com",
                                            caption="Optional. With domain-wide delegation set up, the service account reads Drive as this user.",
                                            secret=False,
                                        ),
                                    ],
                                ),
                            ),
                            SourceFieldSelectConfigOption(
                                label="OAuth client and refresh token",
                                value="oauth",
                                fields=cast(
                                    list[FieldType],
                                    [
                                        SourceFieldInputConfig(
                                            name="client_id",
                                            label="Client ID",
                                            type=SourceFieldInputConfigType.TEXT,
                                            required=False,
                                            placeholder="....apps.googleusercontent.com",
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
                                            placeholder="1//...",
                                            caption="A refresh token for your own OAuth client, authorized for the Drive account you want to sync.",
                                            secret=True,
                                        ),
                                    ],
                                ),
                            ),
                        ],
                    ),
                    SourceFieldInputConfig(
                        name="drive_id",
                        label="Shared drive ID",
                        type=SourceFieldInputConfigType.TEXT,
                        required=False,
                        placeholder="",
                        caption="Optional. Limits the files table to one shared drive. Leave empty to sync every file the credentials can see.",
                        secret=False,
                    ),
                ],
            ),
        )

    def get_canonical_descriptions(self) -> CanonicalDescriptions:
        from products.warehouse_sources.backend.temporal.data_imports.sources.google_drive.canonical_descriptions import (
            CANONICAL_DESCRIPTIONS,
        )

        return CANONICAL_DESCRIPTIONS

    def get_non_retryable_errors(self) -> dict[str, str | None]:
        return {
            "401 Client Error": "Google Drive rejected these credentials. They may have been revoked or rotated — reconnect the source with a fresh key or refresh token.",
            "403 Client Error": "These credentials cannot read Drive. Grant the drive.readonly scope, enable the Google Drive API in the Google Cloud project, and reconnect.",
            # Raised while minting the access token: a revoked refresh token, a rotated service
            # account key, or delegation that was withdrawn. None of it recovers on retry.
            "Google Drive authentication failed": "PostHog could not get an access token for Google Drive. Check the service account key or refresh token, then reconnect.",
            MISSING_SERVICE_ACCOUNT_KEY_ERROR: "No Google Drive service account key is configured. Please update the source configuration.",
            MISSING_OAUTH_CREDENTIALS_ERROR: "The Google Drive client ID, client secret, or refresh token is missing. Please update the source configuration.",
            "Invalid Google Drive service account key": None,
        }

    def get_retryable_errors(self) -> set[str]:
        # Drive reports quota exhaustion as a 403; the transport retries it with backoff and only
        # re-raises once the budget is spent, so Temporal retrying the activity is the right outcome.
        return {"Google Drive rate limit"}

    def _get_auth(self, config: GoogleDriveSourceConfig) -> GoogleDriveAuth:
        if config.auth_method.selection == "oauth":
            if not config.auth_method.client_id or not config.auth_method.client_secret:
                raise ValueError(MISSING_OAUTH_CREDENTIALS_ERROR)
            if not config.auth_method.refresh_token:
                raise ValueError(MISSING_OAUTH_CREDENTIALS_ERROR)
            return GoogleDriveAuth(
                client_id=config.auth_method.client_id,
                client_secret=config.auth_method.client_secret,
                refresh_token=config.auth_method.refresh_token,
            )

        if not config.auth_method.service_account_key:
            raise ValueError(MISSING_SERVICE_ACCOUNT_KEY_ERROR)
        return GoogleDriveAuth(
            service_account_key=config.auth_method.service_account_key,
            impersonated_user_email=config.auth_method.impersonated_user_email or None,
        )

    def get_schemas(
        self,
        config: GoogleDriveSourceConfig,
        team_id: int,
        with_counts: bool = False,
        names: list[str] | None = None,
        force_refresh: bool = False,
        api_version: str | None = None,
    ) -> list[SourceSchema]:
        schemas = [
            SourceSchema(
                name=name,
                supports_incremental=endpoint.supports_incremental,
                supports_append=endpoint.supports_incremental,
                incremental_fields=endpoint.incremental_fields,
                description=ENDPOINT_DESCRIPTIONS.get(name),
                detected_primary_keys=list(endpoint.primary_keys),
            )
            for name, endpoint in GOOGLE_DRIVE_ENDPOINTS.items()
        ]

        if names is not None:
            names_set = set(names)
            schemas = [schema for schema in schemas if schema.name in names_set]

        return schemas

    def validate_credentials(
        self,
        config: GoogleDriveSourceConfig,
        team_id: int,
        schema_name: Optional[str] = None,
        api_version: str | None = None,
    ) -> tuple[bool, str | None]:
        try:
            auth = self._get_auth(config)
        except ValueError as e:
            raw = str(e)
            return False, self.get_non_retryable_errors().get(raw) or raw

        return validate_google_drive_credentials(auth, self.resolve_api_version(api_version))

    def get_resumable_source_manager(self, inputs: SourceInputs) -> ResumableSourceManager[GoogleDriveResumeConfig]:
        return ResumableSourceManager[GoogleDriveResumeConfig](inputs, GoogleDriveResumeConfig)

    def source_for_pipeline(
        self,
        config: GoogleDriveSourceConfig,
        resumable_source_manager: ResumableSourceManager[GoogleDriveResumeConfig],
        inputs: SourceInputs,
    ) -> SourceResponse:
        return google_drive_source(
            auth=self._get_auth(config),
            endpoint=inputs.schema_name,
            api_version=self.resolve_api_version(inputs.api_version),
            source_logger=inputs.logger,
            resumable_source_manager=resumable_source_manager,
            drive_id=config.drive_id or None,
            should_use_incremental_field=inputs.should_use_incremental_field,
            db_incremental_field_last_value=inputs.db_incremental_field_last_value
            if inputs.should_use_incremental_field
            else None,
            incremental_field=inputs.incremental_field,
        )
