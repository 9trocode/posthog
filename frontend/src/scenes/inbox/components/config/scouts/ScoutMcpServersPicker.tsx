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
    /** Compact rows matching the inline scout settings form; the default suits the create dialog. */
    compact?: boolean
}

/**
 * The user's connected MCP servers, each with a switch sharing it with the Scout agent.
 * A grant is per member and per agent, not per scout: scout runs mount their creator's
 * grants, so sharing here applies to every scout the user creates.
 */
export function ScoutMcpServersPicker({ compact = false }: ScoutMcpServersPickerProps): JSX.Element | null {
    const { featureFlags } = useValues(featureFlagLogic)
    if (!featureFlags[FEATURE_FLAGS.MCP_SERVERS]) {
        return null
    }
    return compact ? <CompactPicker /> : <FullPicker />
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
    visibleServers: MCPGatewayServerApi[]
}

function usePickerState(): PickerState {
    const [showAll, setShowAll] = useState(false)
    const { connectedGatewayServers, gatewayServersLoading, scoutAccount, scoutServersLoading } =
        useValues(scoutMcpServersLogic)

    const initialLoading =
        (gatewayServersLoading && connectedGatewayServers.length === 0) ||
        (scoutServersLoading && scoutAccount === null)
    const visibleServers = showAll ? connectedGatewayServers : connectedGatewayServers.slice(0, MAX_VISIBLE_SERVERS)
    return {
        hiddenCount: connectedGatewayServers.length - visibleServers.length,
        initialLoading,
        shareDisabledReason:
            scoutAccount === null && !scoutServersLoading ? 'Scout MCP access is unavailable' : undefined,
        showAll,
        setShowAll,
        visibleServers,
    }
}

function ShareSwitch({
    server,
    size,
    disabledReason,
}: {
    server: MCPGatewayServerApi
    size?: 'small'
    disabledReason: string | undefined
}): JSX.Element {
    const { scoutSharedServerIds, serverShareLoadingIds } = useValues(scoutMcpServersLogic)
    const { setScoutServerShared } = useActions(scoutMcpServersLogic)
    return (
        <LemonSwitch
            size={size}
            checked={scoutSharedServerIds.has(server.id)}
            loading={serverShareLoadingIds.has(server.id)}
            disabledReason={serverShareLoadingIds.has(server.id) ? 'Saving' : disabledReason}
            onChange={(checked) => setScoutServerShared(server.id, checked)}
            aria-label={`Share ${server.name} with your scouts`}
        />
    )
}

function FullPicker(): JSX.Element {
    useMountedLogic(scoutMcpServersLogic)
    const { scoutAccount, teammateScoutServers } = useValues(scoutMcpServersLogic)
    const { hiddenCount, initialLoading, shareDisabledReason, visibleServers, setShowAll, showAll } = usePickerState()

    let body: JSX.Element
    if (initialLoading) {
        body = (
            <div className="flex items-center gap-2 rounded border border-dashed px-3 py-4 text-sm text-secondary">
                <Spinner /> Loading your MCP servers...
            </div>
        )
    } else if (visibleServers.length === 0) {
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
                    {visibleServers.map((server) => {
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
                                <ShareSwitch server={server} disabledReason={shareDisabledReason} />
                            </div>
                        )
                    })}
                </div>
                {!showAll && hiddenCount > 0 && (
                    <button
                        type="button"
                        onClick={() => setShowAll(true)}
                        className="w-full border-t px-3 py-2 text-left text-xs text-secondary transition-colors hover:bg-bg-3000 hover:text-default"
                    >
                        See {hiddenCount} more
                    </button>
                )}
                {scoutAccount?.status === 'paused' && (
                    <div className="border-t px-3 py-2 text-xs text-secondary">Scout MCP access is paused.</div>
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
                    Servers you share are available to every scout you create.
                </p>
            </div>
            {body}
            {teammateScoutServers.length > 0 && (
                <span className="text-xs text-muted">
                    Your scouts can also use {pluralize(teammateScoutServers.length, 'server')} teammates shared to the
                    team.
                </span>
            )}
        </div>
    )
}

function CompactPicker(): JSX.Element {
    useMountedLogic(scoutMcpServersLogic)
    const { hiddenCount, initialLoading, shareDisabledReason, visibleServers, setShowAll, showAll } = usePickerState()

    return (
        <div className="flex flex-col gap-2 border-t border-primary pt-2">
            <div className="flex flex-col min-w-0">
                <span className="text-xs text-default">MCP servers</span>
                <span className="text-[11.5px] text-muted">
                    Shared servers apply to every scout you create.{' '}
                    <Link to={urls.mcpGateway()}>Manage MCP servers</Link>
                </span>
            </div>
            {initialLoading ? (
                <span className="flex items-center gap-2 text-[11.5px] text-muted">
                    <Spinner /> Loading your MCP servers...
                </span>
            ) : visibleServers.length === 0 ? (
                <span className="text-[11.5px] text-muted">
                    <Link to={urls.mcpGateway()}>Connect an MCP server</Link> to give your scouts external tools.
                </span>
            ) : (
                <div className="flex flex-col gap-1.5">
                    {visibleServers.map((server) => (
                        <div key={server.id} className="flex items-center gap-2">
                            <ServerIcon iconDomain={server.icon_domain} serverUrl={server.url} size={20} />
                            <span className="min-w-0 flex-1 truncate text-xs text-default">{server.name}</span>
                            <ShareSwitch server={server} size="small" disabledReason={shareDisabledReason} />
                        </div>
                    ))}
                    {!showAll && hiddenCount > 0 && (
                        <button
                            type="button"
                            onClick={() => setShowAll(true)}
                            className="w-fit text-left text-[11.5px] text-muted transition-colors hover:text-default"
                        >
                            See {hiddenCount} more
                        </button>
                    )}
                </div>
            )}
        </div>
    )
}
