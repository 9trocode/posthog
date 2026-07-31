import type { ErrorTrackingLinkedReportApi } from '../../../signals/frontend/generated/api.schemas'

// Statuses here cover the tag palette the linked-report rows map to, plus a report that has no
// title yet (not summarized) and one with an implementation PR.
export const linkedInboxReports: ErrorTrackingLinkedReportApi[] = [
    {
        id: '019e64b8-0000-7000-8000-000000000001',
        title: 'fix(invites): validate recipient payload before submit',
        status: 'ready',
        created_at: '2024-07-08T10:00:00Z',
        implementation_pr_url: 'https://github.com/PostHog/posthog/pull/12001',
    },
    {
        id: '019e64b8-0000-7000-8000-000000000002',
        title: 'perf(dashboards): paginate cohort filter resolution',
        status: 'in_progress',
        created_at: '2024-07-08T09:00:00Z',
        implementation_pr_url: null,
    },
    {
        id: '019e64b8-0000-7000-8000-000000000003',
        title: null,
        status: 'potential',
        created_at: '2024-07-07T09:00:00Z',
        implementation_pr_url: null,
    },
]
