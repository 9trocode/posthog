import { expectLogic } from 'kea-test-utils'

import { useMocks } from '~/mocks/jest'
import { initKeaTests } from '~/test/init'

import { errorTrackingIssueSceneLogic } from './errorTrackingIssueSceneLogic'

describe('errorTrackingIssueSceneLogic', () => {
    let logic: ReturnType<typeof errorTrackingIssueSceneLogic.build>

    beforeEach(() => {
        useMocks({
            get: {
                '/api/environments/:team_id/error_tracking/issues/:id/': {},
                '/api/environments/:team_id/error_tracking/issues/:id/fingerprints/': [],
            },
            post: {
                '/api/environments/:team_id/query/': { results: [] },
            },
        })
        initKeaTests()
        logic = errorTrackingIssueSceneLogic({ id: 'issue-1' })
        logic.mount()
    })

    afterEach(() => logic?.unmount())

    // eventsQuery filters directly on $exception_issue_id rather than assembling a fingerprint IN
    // list, so it can't drift out of sync with the issue the fingerprints table separately tracks.
    it('filters events by the issue id, not by fingerprints', () => {
        expect(logic.values.eventsQuery.where).toEqual([
            expect.stringContaining("properties.$exception_issue_id = 'issue-1'"),
        ])
    })

    // eventsQueryKey is the kea key of the events table's data source logic: every key change
    // unmounts and remounts the whole table tree. It used to be uuid() per recompute, so even a
    // deep-equal recompute (e.g. re-setting an equal-but-freshly-constructed dateRange) rebuilt
    // the table. These lock in the key contract both ways.
    it('keeps eventsQuery and eventsQueryKey stable across deep-equal dateRange recomputes', () => {
        const initialQuery = logic.values.eventsQuery
        const initialKey = logic.values.eventsQueryKey

        // Freshly constructed but deep-equal — as a URL-driven update would deliver.
        logic.actions.setDateRange({ ...logic.values.dateRange })

        expect(logic.values.eventsQuery).toBe(initialQuery)
        expect(logic.values.eventsQueryKey).toBe(initialKey)
    })

    it.each<[string, (logic: ReturnType<typeof errorTrackingIssueSceneLogic.build>) => void]>([
        ['date range changes', (l) => l.actions.setDateRange({ date_from: '-30d', date_to: null })],
        ['search query changes', (l) => l.actions.setSearchQuery('needle')],
    ])('changes eventsQueryKey when the %s', (_name, mutate) => {
        const initialKey = logic.values.eventsQueryKey

        mutate(logic)

        expect(logic.values.eventsQueryKey).not.toBe(initialKey)
    })

    it('handles an empty initial event query result', async () => {
        await expectLogic(logic, () => {
            logic.actions.loadInitialEvent('2026-01-01T00:00:00Z')
        })
            .toDispatchActions(['loadInitialEventSuccess'])
            .toMatchValues({ initialEvent: null })
    })

    // A malformed `timestamp` URL param used to be stored and fed to getNarrowDateRange, where
    // dayjs().toISOString() threw a RangeError and crashed the whole scene on mount. It must now
    // be ignored so the scene falls back to the valid server-provided timestamp.
    it.each(['not-a-date', 'undefined', '2026-01-02T03%3A04%3A05'])(
        'ignores a malformed initial event timestamp (%s)',
        (bad) => {
            logic.actions.setInitialEventTimestamp(bad)
            expect(logic.values.initialEventTimestamp).toBeNull()

            // A valid timestamp (as the server's last_seen provides) is still accepted afterwards.
            logic.actions.setInitialEventTimestamp('2026-01-02T03:04:05Z')
            expect(logic.values.initialEventTimestamp).toBe('2026-01-02T03:04:05Z')
        }
    )
})
