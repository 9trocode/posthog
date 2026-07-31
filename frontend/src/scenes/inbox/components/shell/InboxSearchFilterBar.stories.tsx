import type { Meta, StoryObj } from '@storybook/react'
import { screen, within } from '@testing-library/dom'
import userEvent from '@testing-library/user-event'

import { inboxFiltersLogic } from '../../logics/inboxFiltersLogic'
import { InboxSearchFilterBar } from './InboxSearchFilterBar'

// The filter menus only exist while open, so each story opens one. The bar's resting state is
// already covered by the Inbox scene stories.

const meta: Meta<typeof InboxSearchFilterBar> = {
    title: 'Scenes-App/Inbox/Filter bar',
    component: InboxSearchFilterBar,
    parameters: { layout: 'fullscreen', viewMode: 'story' },
    decorators: [
        function FrameDecorator(Story) {
            // Reserve room below the bar so the open menu lands inside the snapshot.
            return (
                <div className="bg-primary p-4 min-h-[420px]">
                    <Story />
                </div>
            )
        },
    ],
}
export default meta

type Story = StoryObj<typeof InboxSearchFilterBar>

/**
 * `inboxFiltersLogic` persists the filters to localStorage, so without this a story would render
 * whatever the previously-run story left behind.
 */
function resetFilters(): void {
    const logic = inboxFiltersLogic.findMounted()
    logic?.actions.setSourceProductFilter([])
    logic?.actions.setPriorityFilter([])
    logic?.actions.setSort('priority', 'asc')
}

async function openFilter(canvasElement: HTMLElement, label: string): Promise<void> {
    const canvas = within(canvasElement)
    await userEvent.click(await canvas.findByRole('button', { name: new RegExp(`^${label}:`) }))
}

export const SortMenuOpen: Story = {
    play: async ({ canvasElement }) => {
        resetFilters()
        await openFilter(canvasElement, 'Sort')
    },
}

// Multi-select: picking values keeps the menu open and reveals "Clear all", and the trigger chip
// switches to its active styling with the selected codes.
export const PriorityMenuOpen: Story = {
    play: async ({ canvasElement }) => {
        resetFilters()
        await openFilter(canvasElement, 'Priority')
        // The menu renders into a portal, so query at the document level rather than the canvas.
        await userEvent.click(await screen.findByRole('button', { name: /^P0/ }))
        await userEvent.click(await screen.findByRole('button', { name: /^P1/ }))
    },
}
