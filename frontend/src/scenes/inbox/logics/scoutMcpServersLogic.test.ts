/* oxlint-disable react-hooks/rules-of-hooks -- useMocks is a test helper, not a React hook */
import { MOCK_DEFAULT_USER } from 'lib/api.mock'

import { expectLogic } from 'kea-test-utils'

import { userLogic } from 'scenes/userLogic'

import { useMocks } from '~/mocks/jest'
import { initKeaTests } from '~/test/init'

import type {
    ConnectionStateEnumApi,
    MCPGatewayServerApi,
    MCPServiceAccountApi,
    MCPServiceAccountServerApi,
    ServiceAccountAccessUpdateApi,
    UserBasicApi,
} from 'products/mcp_store/frontend/generated/api.schemas'

import { scoutMcpServersLogic } from './scoutMcpServersLogic'

const YOU: UserBasicApi = {
    id: MOCK_DEFAULT_USER.id,
    uuid: MOCK_DEFAULT_USER.uuid,
    email: MOCK_DEFAULT_USER.email,
    hedgehog_config: null,
}

const TEAMMATE: UserBasicApi = {
    id: MOCK_DEFAULT_USER.id + 1,
    uuid: 'teammate-uuid',
    email: 'teammate@posthog.com',
    hedgehog_config: null,
}

function server(
    id: string,
    name: string,
    connectionState: ConnectionStateEnumApi,
    sharedBy: UserBasicApi = YOU,
    scope: MCPServiceAccountServerApi['scope'] = 'personal'
): MCPServiceAccountServerApi {
    return {
        id,
        shared_by: sharedBy,
        scope,
        name,
        description: `${name} workspace`,
        icon_key: name.toLowerCase(),
        icon_domain: `${name.toLowerCase()}.com`,
        connection_state: connectionState,
    }
}

function account(
    agentKey: MCPServiceAccountApi['agent_key'],
    servers: MCPServiceAccountServerApi[],
    { status = 'active' }: { status?: MCPServiceAccountApi['status'] } = {}
): MCPServiceAccountApi {
    return {
        id: `${agentKey}-id`,
        name: agentKey,
        description: `${agentKey} agent`,
        handle: `svc-${agentKey}`,
        agent_key: agentKey,
        status,
        server_ids: servers.map(({ id }) => id),
        servers,
        last_active_at: null,
        created_at: '2026-07-22T00:00:00Z',
        updated_at: '2026-07-22T00:00:00Z',
    }
}

function gatewayServer(
    id: string,
    name: string,
    { connected = true }: { connected?: boolean } = {}
): MCPGatewayServerApi {
    return {
        id,
        name,
        url: `https://${name.toLowerCase()}.com/mcp`,
        description: `${name} workspace`,
        category: 'dev',
        template_auth_type: 'oauth',
        is_team_enabled: true,
        icon_key: name.toLowerCase(),
        icon_domain: `${name.toLowerCase()}.com`,
        docs_url: '',
        template_id: null,
        tool_count: 3,
        connections: [],
        your_connection: connected
            ? {
                  installation_id: `${id}-installation`,
                  is_enabled: true,
                  pending_oauth: false,
                  needs_reauth: false,
                  last_used_at: null,
              }
            : null,
        agents: [],
        revoked_user_ids: [],
        is_revoked_for_you: false,
        created_by: null,
        created_at: '2026-07-22T00:00:00Z',
        updated_at: '2026-07-22T00:00:00Z',
    }
}

function listResponse<T>(results: T[]): [number, { count: number; next: null; previous: null; results: T[] }] {
    return [200, { count: results.length, next: null, previous: null, results }]
}

describe('scoutMcpServersLogic', () => {
    let logic: ReturnType<typeof scoutMcpServersLogic.build> | undefined

    beforeEach(() => {
        initKeaTests()
    })

    afterEach(() => {
        logic?.unmount()
    })

    it('separates your Scout grants from teammate team shares', async () => {
        const notion = server('notion-id', 'Notion', 'missing_credential')
        const linear = server('linear-id', 'Linear', 'ready')
        const teammateGithub = server('github-id', 'GitHub', 'ready', TEAMMATE, 'team')
        const teammateSentry = server('sentry-id', 'Sentry', 'ready', TEAMMATE)
        const zendesk = server('zendesk-id', 'Zendesk', 'ready')
        useMocks({
            get: {
                '/api/projects/:team_id/mcp_gateway/service_accounts/': () =>
                    listResponse([
                        account('support', [zendesk]),
                        account('scout', [notion, linear, teammateGithub, teammateSentry]),
                    ]),
                '/api/projects/:team_id/mcp_gateway/servers/': () => listResponse([]),
            },
        })

        logic = scoutMcpServersLogic()
        logic.mount()
        await expectLogic(logic).toFinishAllListeners()

        expect(logic.values.scoutServers).toEqual([notion, linear, teammateGithub, teammateSentry])
        expect(logic.values.yourScoutServers).toEqual([notion, linear])
        expect(logic.values.teammateScoutServers).toEqual([teammateGithub])
        expect(logic.values.scoutSharedServerIds).toEqual(new Set(['notion-id', 'linear-id']))
    })

    it('attributes no grants while the current user is still loading', async () => {
        const linear = server('linear-id', 'Linear', 'ready')
        const teammateGithub = server('github-id', 'GitHub', 'ready', TEAMMATE)
        useMocks({
            get: {
                '/api/projects/:team_id/mcp_gateway/service_accounts/': () =>
                    listResponse([account('scout', [linear, teammateGithub])]),
                '/api/projects/:team_id/mcp_gateway/servers/': () => listResponse([]),
            },
        })

        logic = scoutMcpServersLogic()
        logic.mount()
        await expectLogic(logic).toFinishAllListeners()
        userLogic.actions.loadUserSuccess(null)

        expect(logic.values.currentUserId).toBeNull()
        expect(logic.values.yourScoutServers).toEqual([])
        expect(logic.values.teammateScoutServers).toEqual([])
        expect(logic.values.scoutSharedServerIds).toEqual(new Set())
    })

    it('lists only servers you connected, sorted alphabetically regardless of case', async () => {
        useMocks({
            get: {
                '/api/projects/:team_id/mcp_gateway/service_accounts/': () => listResponse([account('scout', [])]),
                '/api/projects/:team_id/mcp_gateway/servers/': () =>
                    listResponse([
                        gatewayServer('notion-id', 'Notion'),
                        gatewayServer('zendesk-id', 'Zendesk', { connected: false }),
                        gatewayServer('linear-id', 'linear'),
                    ]),
            },
        })

        logic = scoutMcpServersLogic()
        logic.mount()
        await expectLogic(logic).toFinishAllListeners()

        expect(logic.values.connectedGatewayServers.map(({ id }) => id)).toEqual(['linear-id', 'notion-id'])
    })

    it('shares and unshares with the scope of the existing grant and adopts the returned account', async () => {
        const accessBodies: ServiceAccountAccessUpdateApi[] = []
        const teamNotion = server('notion-id', 'Notion', 'ready', YOU, 'team')
        let scoutAccount = account('scout', [teamNotion])
        useMocks({
            get: {
                '/api/projects/:team_id/mcp_gateway/service_accounts/': () => listResponse([scoutAccount]),
                '/api/projects/:team_id/mcp_gateway/servers/': () =>
                    listResponse([gatewayServer('notion-id', 'Notion'), gatewayServer('linear-id', 'Linear')]),
            },
            post: {
                '/api/projects/:team_id/mcp_gateway/service_accounts/:id/access/': async ({ request }) => {
                    const body = (await request.json()) as ServiceAccountAccessUpdateApi
                    accessBodies.push(body)
                    const grants = body.enabled
                        ? [...scoutAccount.servers, server(body.gateway_server_id, 'Linear', 'ready')]
                        : scoutAccount.servers.filter(({ id }) => id !== body.gateway_server_id)
                    scoutAccount = account('scout', [...grants])
                    return [200, scoutAccount]
                },
            },
        })

        logic = scoutMcpServersLogic()
        logic.mount()
        await expectLogic(logic).toFinishAllListeners()

        // A fresh share defaults to personal scope; removing a team share must keep sending
        // its scope so the endpoint's personal default can't demote anything.
        logic.actions.setScoutServerShared('linear-id', true)
        await expectLogic(logic).toFinishAllListeners()
        logic.actions.setScoutServerShared('notion-id', false)
        await expectLogic(logic).toFinishAllListeners()

        expect(accessBodies).toEqual([
            { gateway_server_id: 'linear-id', enabled: true, scope: 'personal' },
            { gateway_server_id: 'notion-id', enabled: false, scope: 'team' },
        ])
        expect(logic.values.scoutSharedServerIds).toEqual(new Set(['linear-id']))
        expect(logic.values.serverShareLoadingIds).toEqual(new Set())
    })
})
