import type { Meta, StoryObj } from '@storybook/react'
import { useState } from 'react'

import { FEATURE_FLAGS } from 'lib/constants'

import { mswDecorator } from '~/mocks/browser'

import { ScoutMcpServersPicker } from './ScoutMcpServersPicker'

/** Stories drive the picker like its call sites do: selection state lives outside. */
function ControlledPicker({ compact }: { compact?: boolean }): JSX.Element {
    const [selectedServerIds, setSelectedServerIds] = useState<string[]>(['linear-id'])
    return (
        <ScoutMcpServersPicker
            compact={compact}
            selectedServerIds={selectedServerIds}
            onChange={setSelectedServerIds}
        />
    )
}

const YOU = { id: 179, uuid: 'you-uuid', email: 'you@posthog.com', hedgehog_config: null }
const TEAMMATE = { id: 2, uuid: 'mate-uuid', email: 'mate@posthog.com', hedgehog_config: null }

function grant(id: string, name: string, sharedBy = YOU, scope = 'personal'): Record<string, unknown> {
    return {
        id,
        shared_by: sharedBy,
        scope,
        name,
        description: `${name} workspace`,
        icon_key: name.toLowerCase(),
        icon_domain: `${name.toLowerCase()}.com`,
        connection_state: 'ready',
    }
}

function gatewayServer(
    id: string,
    name: string,
    connection: Record<string, unknown> | null = {
        installation_id: `${id}-installation`,
        is_enabled: true,
        pending_oauth: false,
        needs_reauth: false,
        last_used_at: null,
    }
): Record<string, unknown> {
    return {
        id,
        name,
        url: `https://${name.toLowerCase()}.com/mcp`,
        description: `${name} workspace tools`,
        category: 'dev',
        template_auth_type: 'oauth',
        is_team_enabled: true,
        icon_key: name.toLowerCase(),
        icon_domain: `${name.toLowerCase()}.com`,
        docs_url: '',
        template_id: null,
        tool_count: 3,
        connections: [],
        your_connection: connection,
        agents: [],
        revoked_user_ids: [],
        is_revoked_for_you: false,
        created_by: null,
        created_at: '2026-07-22T00:00:00Z',
        updated_at: '2026-07-22T00:00:00Z',
    }
}

const scoutAccount = {
    id: 'scout-account-id',
    name: 'Scout',
    description: 'Scheduled scouts',
    handle: 'svc-scout',
    agent_key: 'scout',
    status: 'active',
    server_ids: ['linear-id', 'notion-id'],
    servers: [
        grant('linear-id', 'Linear'),
        grant('notion-id', 'Notion'),
        grant('github-id', 'GitHub', TEAMMATE, 'team'),
    ],
    last_active_at: null,
    created_at: '2026-07-22T00:00:00Z',
    updated_at: '2026-07-22T00:00:00Z',
}

const meta: Meta<typeof ScoutMcpServersPicker> = {
    title: 'Scenes-App/Inbox/ScoutMcpServersPicker',
    component: ScoutMcpServersPicker,
    decorators: [
        mswDecorator({
            get: {
                '/api/projects/:id/mcp_gateway/service_accounts/': () => [
                    200,
                    { count: 1, next: null, previous: null, results: [scoutAccount] },
                ],
                '/api/projects/:id/mcp_gateway/servers/': () => [
                    200,
                    {
                        count: 5,
                        next: null,
                        previous: null,
                        results: [
                            gatewayServer('notion-id', 'Notion'),
                            gatewayServer('linear-id', 'Linear'),
                            gatewayServer('stripe-id', 'Stripe', {
                                installation_id: 'stripe-installation',
                                is_enabled: true,
                                pending_oauth: false,
                                needs_reauth: true,
                                last_used_at: null,
                            }),
                            gatewayServer('slack-id', 'Slack'),
                            gatewayServer('zendesk-id', 'Zendesk', null),
                        ],
                    },
                ],
            },
        }),
    ],
    parameters: {
        testOptions: { waitForLoadersToDisappear: true },
        featureFlags: [FEATURE_FLAGS.MCP_SERVERS],
    },
}
export default meta
type Story = StoryObj<typeof ScoutMcpServersPicker>

export const CreateDialogVariant: Story = {
    render: () => (
        <div className="max-w-2xl p-4">
            <ControlledPicker />
        </div>
    ),
}

export const ScoutSettingsVariant: Story = {
    render: () => (
        <div className="max-w-md p-4">
            <ControlledPicker compact />
        </div>
    ),
}
