import { ProductKey } from '~/queries/schema/schema-general'

import { FilterLogicalOperator } from '../../../frontend/src/types'
import { errorTrackingIssueBreakdownQuery, errorTrackingIssueEventsQuery, errorTrackingQuery } from './queries'

describe('queries', () => {
    describe('errorTrackingQuery', () => {
        describe('usage in web analytics', () => {
            it('should return a query with the correct properties', () => {
                const actual = errorTrackingQuery({
                    orderBy: 'users',
                    dateRange: { date_from: '-7d', date_to: null },
                    filterTestAccounts: true,
                    filterGroup: {
                        type: FilterLogicalOperator.And,
                        values: [
                            {
                                type: FilterLogicalOperator.And,
                                values: [],
                            },
                        ],
                    },
                    columns: ['error', 'users', 'occurrences'],
                    limit: 4,
                    volumeResolution: 20,
                    personId: undefined,
                })
                expect(actual).toMatchSnapshot()
            })
        })
    })

    describe('error tracking query tags', () => {
        it('tags issue event queries as error tracking', () => {
            const actual = errorTrackingIssueEventsQuery({
                issueId: 'issue-1',
                filterTestAccounts: false,
                filterGroup: {
                    type: FilterLogicalOperator.And,
                    values: [
                        {
                            type: FilterLogicalOperator.And,
                            values: [],
                        },
                    ],
                },
                searchQuery: '',
                dateRange: { date_from: '-7d', date_to: null },
                columns: ['*'],
            })

            expect(actual.tags).toEqual({ productKey: ProductKey.ERROR_TRACKING })
        })

        // The Occurrences tab used to filter on a fingerprint IN list assembled from a separate
        // Postgres fetch, which could drift from the issue the ClickHouse-backed list already
        // agreed on (e.g. after a merge or split rewrote one side but not the other) and silently
        // empty the tab. Filtering directly on the issue id removes that drift entirely.
        it('filters events by issue id rather than a fingerprint list', () => {
            const actual = errorTrackingIssueEventsQuery({
                issueId: "issue_with_'quote",
                filterTestAccounts: false,
                filterGroup: {
                    type: FilterLogicalOperator.And,
                    values: [
                        {
                            type: FilterLogicalOperator.And,
                            values: [],
                        },
                    ],
                },
                searchQuery: "O'Brien",
                dateRange: { date_from: '-7d', date_to: null },
                columns: ['*'],
            })

            const where = (actual.where ?? []).join(' ')
            expect(where).toContain("properties.$exception_issue_id = 'issue_with_\\'quote'")
            expect(where).toContain("'%O\\'Brien%'")
        })

        it('tags issue breakdown insight queries as error tracking', () => {
            const actual = errorTrackingIssueBreakdownQuery({
                breakdownProperty: '$browser',
                dateRange: { date_from: '-7d', date_to: null },
                filterTestAccounts: false,
                filterGroup: {
                    type: FilterLogicalOperator.And,
                    values: [
                        {
                            type: FilterLogicalOperator.And,
                            values: [],
                        },
                    ],
                },
                issueId: 'issue-id',
            })

            expect(actual.source.tags).toEqual({ productKey: ProductKey.ERROR_TRACKING })
        })
    })
})
