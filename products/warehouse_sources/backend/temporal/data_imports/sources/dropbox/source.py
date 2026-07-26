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
from products.warehouse_sources.backend.temporal.data_imports.sources.dropbox.dropbox import (
    DropboxCredentials,
    DropboxResumeConfig,
    check_endpoint_access,
    dropbox_source,
    validate_credentials as validate_dropbox_credentials,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.dropbox.settings import (
    DESCRIPTIONS,
    ENDPOINTS,
    INCREMENTAL_FIELDS,
    SHOULD_SYNC_DEFAULT,
)
from products.warehouse_sources.backend.temporal.data_imports.sources.generated_configs.dropbox import (
    DropboxSourceConfig,
)
from products.warehouse_sources.backend.types import ExternalDataSourceType


@SourceRegistry.register
class DropboxSource(ResumableSource[DropboxSourceConfig, DropboxResumeConfig]):
    api_docs_url = "https://www.dropbox.com/developers/documentation/http/documentation"
    supported_versions = ("v2",)
    default_version = "v2"

    lists_tables_without_credentials = True  # static endpoint catalog — safe for public docs

    @property
    def source_type(self) -> ExternalDataSourceType:
        return ExternalDataSourceType.DROPBOX

    def get_non_retryable_errors(self) -> dict[str, str | None]:
        return {
            "400 Client Error: Bad Request for url: https://api.dropboxapi.com/oauth2/token": "Dropbox rejected your refresh token. It may have been revoked, or it may belong to a different app.",
            "401 Client Error: Unauthorized for url: https://api.dropboxapi.com/oauth2/token": "Dropbox rejected your app key and app secret. Check them in the Dropbox App Console.",
            "Dropbox did not return an access token": "Dropbox rejected the token refresh. Reconnect the source with a new refresh token.",
            "401 Client Error: Unauthorized for url: https://api.dropboxapi.com/2/": "Dropbox rejected the access token. Reconnect the source.",
            "403 Client Error: Forbidden for url: https://api.dropboxapi.com/2/": "Your Dropbox app is missing a scope for this table. Team tables also need a team-scoped app.",
            "409 Client Error: Conflict for url: https://api.dropboxapi.com/2/": "Dropbox rejected the request. Check that the folder path exists and that the account can reach it.",
        }

    @property
    def get_source_config(self) -> SourceConfig:
        return SourceConfig(
            name=SchemaExternalDataSourceType.DROPBOX,
            category=DataWarehouseSourceCategory.FILE_STORAGE,
            label="Dropbox",
            caption="""Connect Dropbox to pull file, folder, and sharing metadata into the PostHog Data warehouse.

Create an app in the [Dropbox App Console](https://www.dropbox.com/developers/apps), then enter its app key and app secret along with a refresh token authorized for your account. Dropbox access tokens last only a few hours, so a refresh token is required.

Grant `files.metadata.read` for the files table and `sharing.read` for shared links and folders. The team tables need a team-scoped app with `members.read` and `events.read`.""",
            iconPath="/static/services/dropbox.png",
            docsUrl="https://posthog.com/docs/cdp/sources/dropbox",
            releaseStatus=ReleaseStatus.ALPHA,
            fields=cast(
                list[FieldType],
                [
                    SourceFieldInputConfig(
                        name="app_key",
                        label="App key",
                        type=SourceFieldInputConfigType.TEXT,
                        required=True,
                        placeholder="",
                        secret=False,
                    ),
                    SourceFieldInputConfig(
                        name="app_secret",
                        label="App secret",
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
                    SourceFieldInputConfig(
                        name="folder_path",
                        label="Folder path (optional)",
                        type=SourceFieldInputConfigType.TEXT,
                        required=False,
                        placeholder="/Reports",
                        secret=False,
                    ),
                    SourceFieldInputConfig(
                        name="team_member_id",
                        label="Team member ID (optional)",
                        type=SourceFieldInputConfigType.TEXT,
                        required=False,
                        placeholder="dbmid:...",
                        secret=False,
                    ),
                    SourceFieldInputConfig(
                        name="root_namespace_id",
                        label="Root namespace ID (optional)",
                        type=SourceFieldInputConfigType.TEXT,
                        required=False,
                        placeholder="",
                        secret=False,
                    ),
                ],
            ),
        )

    def get_canonical_descriptions(self) -> CanonicalDescriptions:
        from products.warehouse_sources.backend.temporal.data_imports.sources.dropbox.canonical_descriptions import (
            CANONICAL_DESCRIPTIONS,
        )

        return CANONICAL_DESCRIPTIONS

    def get_schemas(
        self,
        config: DropboxSourceConfig,
        team_id: int,
        with_counts: bool = False,
        names: list[str] | None = None,
        force_refresh: bool = False,
        api_version: str | None = None,
    ) -> list[SourceSchema]:
        return build_endpoint_schemas(
            ENDPOINTS,
            INCREMENTAL_FIELDS,
            names,
            descriptions=DESCRIPTIONS,
            should_sync_default=SHOULD_SYNC_DEFAULT,
        )

    def _credentials(self, config: DropboxSourceConfig) -> DropboxCredentials:
        return DropboxCredentials(
            app_key=config.app_key,
            app_secret=config.app_secret,
            refresh_token=config.refresh_token,
            team_member_id=config.team_member_id,
            root_namespace_id=config.root_namespace_id,
        )

    def validate_credentials(
        self,
        config: DropboxSourceConfig,
        team_id: int,
        schema_name: Optional[str] = None,
        api_version: str | None = None,
    ) -> tuple[bool, str | None]:
        return validate_dropbox_credentials(self._credentials(config))

    def get_endpoint_permissions(
        self,
        config: DropboxSourceConfig,
        team_id: int,
        endpoints: list[str],
        api_version: str | None = None,
    ) -> dict[str, str | None]:
        return check_endpoint_access(self._credentials(config), endpoints)

    def get_resumable_source_manager(self, inputs: SourceInputs) -> ResumableSourceManager[DropboxResumeConfig]:
        return ResumableSourceManager[DropboxResumeConfig](inputs, DropboxResumeConfig)

    def source_for_pipeline(
        self,
        config: DropboxSourceConfig,
        resumable_source_manager: ResumableSourceManager[DropboxResumeConfig],
        inputs: SourceInputs,
    ) -> SourceResponse:
        return dropbox_source(
            credentials=self._credentials(config),
            endpoint=inputs.schema_name,
            folder_path=config.folder_path,
            logger=inputs.logger,
            resumable_source_manager=resumable_source_manager,
            should_use_incremental_field=inputs.should_use_incremental_field,
            db_incremental_field_last_value=inputs.db_incremental_field_last_value
            if inputs.should_use_incremental_field
            else None,
        )
