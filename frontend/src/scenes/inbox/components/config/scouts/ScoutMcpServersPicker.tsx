import { useActions, useMountedLogic, useValues } from 'kea'
import { useState } from 'react'

import { IconChevronRight, IconServer } from '@posthog/icons'
import { LemonSwitch, LemonTag, LemonTagType, Link, Spinner } from '@posthog/lemon-ui'
import { ServerIcon } from '@posthog/products-mcp-store/frontend/scene/icons'

import { FEATURE_FLAGS } from 'lib/constants'
import { featureFlagLogic } from 'lib/logic/featureFlagLogic'
import { pluralize } from 'lib/utils/strings'
import { urls } from 'scenes/urls'

import type { GatewayYourConnectionApi, MCPGatewayServerApi } from 'products/mcp_store/frontend/generated/api.schemas'

import { scoutMcpServersLogic } from '../../../logics/scoutMcpServersLogic'

/** Rows shown before the list collapses behind "See more". */
const MAX_VISIBLE_SERVERS = 2

interface ScoutMcpServersPickerProps {
    /** Gateway server ids selected for this scout (`mcp_gateway_server_ids`). */
    selectedServerIds: string[]
    onChange: (serverIds: string[]) => void
    /** Compact rows matching the inline scout settings form; the default suits the create dialog. */
    compact?: boolean
    disabledReason?: string
}

/**
 * Per-scout selection of the user's connected MCP servers, persisted as the scout config's
 * `mcp_gateway_server_ids`. Selecting a server also shares the user's connection with the
 * Scout agent when it isn't shared yet — the grant makes the connection mountable, the
 * selection decides which scouts mount it.
 */
export function ScoutMcpServersPicker(props: ScoutMcpServersPickerProps): JSX.Element | null {
    const { featureFlags } = useValues(featureFlagLogic)
    if (!featureFlags[FEATURE_FLAGS.MCP_SERVERS]) {
        return null
    }
    return props.compact ? <CompactPicker {...props} /> : <FullPicker {...props} />
}

function connectionIssue(connection: GatewayYourConnectionApi): { label: string; tagType: LemonTagType } | null {
    if (connection.needs_reauth) {
        return { label: 'Reconnect', tagType: 'danger' }
    }
    if (connection.pending_oauth) {
        return { label: 'Pending OAuth', tagType: 'warning' }
    }
    if (!connection.is_enabled) {
        return { label: 'Disabled', tagType: 'muted' }
    }
    return null
}

interface PickerState {
    hiddenCount: number
    initialLoading: boolean
    shareDisabledReason: string | undefined
    showAll: boolean
    setShowAll: (showAll: boolean) => void
    toggleServer: (serverId: string, selected: boolean) => void
    visibleServers: MCPGatewayServerApi[]
}

function usePickerState({ selectedServerIds, onChange, disabledReason }: ScoutMcpServersPickerProps): PickerState {
    const [showAll, setShowAll] = useState(false)
    const { connectedGatewayServers, gatewayServersLoading, scoutAccount, scoutServersLoading } =
        useValues(scoutMcpServersLogic)
    const { ensureScoutServerShared } = useActions(scoutMcpServersLogic)

    const initialLoading =
        (gatewayServersLoading && connectedGatewayServers.length === 0) ||
        (scoutServersLoading && scoutAccount === null)
    const visibleServers = showAll ? connectedGatewayServers : connectedGatewayServers.slice(0, MAX_VISIBLE_SERVERS)
    const toggleServer = (serverId: string, selected: boolean): void => {
        if (selected) {
            // The scout can only mount a selected server once the user's connection is shared
            // with the Scout agent; create that (personal) grant on first selection.
            ensureScoutServerShared(serverId)
        }
        onChange(selected ? [...selectedServerIds, serverId] : selectedServerIds.filter((id) => id !== serverId))
    }
    return {
        hiddenCount: connectedGatewayServers.length - visibleServers.length,
        initialLoading,
        shareDisabledReason:
            disabledReason ??
            (scoutAccount === null && !scoutServersLoading ? 'Scout MCP access is unavailable' : undefined),
        showAll,
        setShowAll,
        toggleServer,
        visibleServers,
    }
}

function SelectSwitch({
    server,
    size,
    state,
    selectedServerIds,
}: {
    server: MCPGatewayServerApi
    size?: 'small'
    state: PickerState
    selectedServerIds: string[]
}): JSX.Element {
    const { serverShareLoadingIds } = useValues(scoutMcpServersLogic)
    return (
        <LemonSwitch
            size={size}
            checked={selectedServerIds.includes(server.id)}
            loading={serverShareLoadingIds.has(server.id)}
            disabledReason={serverShareLoadingIds.has(server.id) ? 'Saving' : state.shareDisabledReason}
            onChange={(checked) => state.toggleServer(server.id, checked)}
            aria-label={`Let this scout use ${server.name}`}
        />
    )
}

function FullPicker(props: ScoutMcpServersPickerProps): JSX.Element {
    useMountedLogic(scoutMcpServersLogic)
    const { scoutAccount, teammateScoutServers } = useValues(scoutMcpServersLogic)
    const state = usePickerState(props)

    let body: JSX.Element
    if (state.initialLoading) {
        body = (
            <div className="flex items-center gap-2 rounded border border-dashed px-3 py-4 text-sm text-secondary">
                <Spinner /> Loading your MCP servers...
            </div>
        )
    } else if (state.visibleServers.length === 0) {
        body = (
            <div className="flex items-start gap-3 rounded border border-dashed px-3 py-3">
                <IconServer className="size-5 shrink-0 mt-0.5 text-secondary" />
                <div className="min-w-0">
                    <div className="font-medium text-sm text-default">No MCP servers connected yet</div>
                    <p className="text-xs text-secondary mt-0.5 mb-0">
                        <Link to={urls.mcpGateway()}>Connect an MCP server</Link> to give your scouts external tools.
                    </p>
                </div>
            </div>
        )
    } else {
        body = (
            <div className="rounded border bg-bg-light overflow-hidden">
                <div className="divide-y">
                    {state.visibleServers.map((server) => {
                        const issue = server.your_connection && connectionIssue(server.your_connection)
                        return (
                            <div key={server.id} className="flex items-center gap-3 px-3 py-2.5">
                                <ServerIcon iconDomain={server.icon_domain} serverUrl={server.url} size={28} />
                                <div className="min-w-0 flex-1">
                                    <div className="font-medium text-sm text-default truncate">{server.name}</div>
                                    {server.description && (
                                        <div className="text-xs text-secondary truncate">{server.description}</div>
                                    )}
                                </div>
                                {issue && (
                                    <LemonTag type={issue.tagType} size="small">
                                        {issue.label}
                                    </LemonTag>
                                )}
                                <SelectSwitch
                                    server={server}
                                    state={state}
                                    selectedServerIds={props.selectedServerIds}
                                />
                            </div>
                        )
                    })}
                </div>
                {!state.showAll && state.hiddenCount > 0 && (
                    <button
                        type="button"
                        onClick={() => state.setShowAll(true)}
                        className="w-full border-t border-primary px-3 py-2 text-left text-xs text-secondary transition-colors hover:bg-bg-3000 hover:text-default"
                    >
                        See {state.hiddenCount} more
                    </button>
                )}
                {scoutAccount?.status === 'paused' && (
                    <div className="border-t border-primary px-3 py-2 text-xs text-secondary">
                        Scout MCP access is paused.
                    </div>
                )}
                <Link
                    to={urls.mcpGateway()}
                    className="group flex items-center justify-between gap-3 border-t border-primary px-3 py-2 text-xs no-underline transition-colors hover:bg-bg-3000"
                >
                    <span className="text-secondary group-hover:text-default">Connect new MCP servers</span>
                    <IconChevronRight className="size-4 shrink-0 text-muted transition-colors group-hover:text-default" />
                </Link>
            </div>
        )
    }

    return (
        <div className="flex flex-col gap-3 border-t border-primary pt-4">
            <div className="flex flex-col gap-0.5">
                <span className="font-medium text-sm">MCP servers</span>
                <p className="text-xs text-secondary mb-0">
                    Choose which of your connected MCP servers this scout can use.
                </p>
            </div>
            {body}
            {teammateScoutServers.length > 0 && (
                <span className="text-xs text-muted">
                    This scout can also use {pluralize(teammateScoutServers.length, 'server')} teammates shared to the
                    team.
                </span>
            )}
        </div>
    )
}

function CompactPicker(props: ScoutMcpServersPickerProps): JSX.Element {
    useMountedLogic(scoutMcpServersLogic)
    const state = usePickerState(props)

    return (
        <div className="flex flex-col gap-2 border-t border-primary pt-2">
            <div className="flex flex-col min-w-0">
                <span className="text-xs text-default">MCP servers</span>
                <span className="text-[11.5px] text-muted">
                    Choose which of your connected MCP servers this scout can use.{' '}
                    <Link to={urls.mcpGateway()}>Manage MCP servers</Link>
                </span>
            </div>
            {state.initialLoading ? (
                <span className="flex items-center gap-2 text-[11.5px] text-muted">
                    <Spinner /> Loading your MCP servers...
                </span>
            ) : state.visibleServers.length === 0 ? (
                <span className="text-[11.5px] text-muted">
                    <Link to={urls.mcpGateway()}>Connect an MCP server</Link> to give your scouts external tools.
                </span>
            ) : (
                <div className="flex flex-col gap-1.5">
                    {state.visibleServers.map((server) => (
                        <div key={server.id} className="flex items-center gap-2">
                            <ServerIcon iconDomain={server.icon_domain} serverUrl={server.url} size={20} />
                            <span className="min-w-0 flex-1 truncate text-xs text-default">{server.name}</span>
                            <SelectSwitch
                                server={server}
                                size="small"
                                state={state}
                                selectedServerIds={props.selectedServerIds}
                            />
                        </div>
                    ))}
                    {!state.showAll && state.hiddenCount > 0 && (
                        <button
                            type="button"
                            onClick={() => state.setShowAll(true)}
                            className="w-fit text-left text-[11.5px] text-muted transition-colors hover:text-default"
                        >
                            See {state.hiddenCount} more
                        </button>
                    )}
                </div>
            )}
        </div>
    )
}
