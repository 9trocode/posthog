import { Meta, StoryObj } from '@storybook/react'

import { App } from 'scenes/App'
import { urls } from 'scenes/urls'

import { mswDecorator } from '~/mocks/browser'

import {
    errorTrackingEventsQueryResponse,
    errorTrackingQueryResponse,
    errorTrackingTypeIssue,
} from './__mocks__/error_tracking_query'
import { linkedInboxReports } from './__mocks__/linked_inbox_reports'

const meta: Meta = {
    component: App,
    title: 'Scenes-App/ErrorTracking',
    parameters: {
        layout: 'fullscreen',
        viewMode: 'story',
        mockDate: '2024-07-09', // To stabilize relative dates
        pageUrl: urls.errorTracking(),
    },
    decorators: [
        mswDecorator({
            get: {
                'api/projects/:team_id/error_tracking/issue/:id': () => [200, errorTrackingTypeIssue],
                // Lets the issue page's scene panel show its linked inbox reports.
                '/api/projects/:team_id/signals/reports/linked_reports/': () => [200, linkedInboxReports],
            },
            post: {
                '/api/environments/:team_id/query/ErrorTrackingQuery': () => [200, errorTrackingQueryResponse],
                '/api/environments/:team_id/query/EventsQuery': () => [200, errorTrackingEventsQueryResponse],
            },
        }),
    ],
}
export default meta

type Story = StoryObj<{}>
export const ListPage: Story = {}
export const GroupPage: Story = { parameters: { pageUrl: urls.errorTrackingIssue('id') } }
