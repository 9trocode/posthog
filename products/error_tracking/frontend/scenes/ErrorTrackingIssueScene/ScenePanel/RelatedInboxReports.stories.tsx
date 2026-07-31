import type { Meta, StoryObj } from '@storybook/react'

import { mswDecorator } from '~/mocks/browser'

import { linkedInboxReports } from '../../../__mocks__/linked_inbox_reports'
import { RelatedInboxReports } from './RelatedInboxReports'

// The section renders nothing when an issue has no linked reports, so the populated state is the
// only one worth pinning here. The issue page as a whole is covered by Scenes-App/ErrorTracking.

const meta: Meta<typeof RelatedInboxReports> = {
    title: 'Scenes-App/ErrorTracking/Related inbox reports',
    component: RelatedInboxReports,
    parameters: { layout: 'padded', viewMode: 'story', mockDate: '2024-07-09' },
    decorators: [
        mswDecorator({
            get: {
                '/api/projects/:team_id/signals/reports/linked_reports/': () => [200, linkedInboxReports],
            },
        }),
    ],
    args: { issueId: 'issue-with-reports' },
}
export default meta

type Story = StoryObj<typeof RelatedInboxReports>

export const WithLinkedReports: Story = {}
