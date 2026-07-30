import type { ChatMessage, Ticket } from '../../types'
import { chatTranscriptMarkdown } from './chatTranscript'

const ticket = {
    id: 'abc-123',
    ticket_number: 65361,
    status: 'open',
    priority: 'high',
    channel_source: 'slack',
    created_at: '2026-07-29T17:23:00Z',
} as Ticket

function message(overrides: Partial<ChatMessage>): ChatMessage {
    return {
        id: 'msg-1',
        content: 'Hello there',
        authorType: 'customer',
        authorName: 'Brendan Cooper',
        createdAt: '2026-07-29T17:23:00Z',
        ...overrides,
    }
}

describe('chatTranscriptMarkdown', () => {
    it('renders a full transcript with header, messages, and separators', () => {
        const markdown = chatTranscriptMarkdown(ticket, [
            message({ content: 'The page locks up when scrolling.' }),
            message({
                id: 'msg-2',
                content: 'Thanks, looking into it now.',
                authorType: 'human',
                authorName: 'Jane Doe',
                createdAt: '2026-07-29T18:01:00Z',
            }),
        ])

        expect(markdown).toBe(
            [
                '# Support ticket #65361',
                '',
                '- Channel: slack',
                '- Status: Open',
                '- Priority: High',
                '- Created: 2026-07-29 17:23 UTC',
                `- URL: http://localhost/support/tickets/65361`,
                '',
                '---',
                '',
                '### Brendan Cooper (Customer) · 2026-07-29 17:23 UTC',
                '',
                'The page locks up when scrolling.',
                '',
                '---',
                '',
                '### Jane Doe (Support) · 2026-07-29 18:01 UTC',
                '',
                'Thanks, looking into it now.',
                '',
            ].join('\n')
        )
    })

    it('marks private notes', () => {
        const markdown = chatTranscriptMarkdown(ticket, [
            message({
                authorType: 'human',
                authorName: 'Jane Doe',
                isPrivate: true,
            }),
        ])
        expect(markdown).toContain('### Jane Doe (Support) · 2026-07-29 17:23 UTC (private note)')
    })

    it('labels AI messages', () => {
        const markdown = chatTranscriptMarkdown(ticket, [
            message({ authorType: 'AI', authorName: 'PostHog Assistant' }),
        ])
        expect(markdown).toContain('### PostHog Assistant (AI)')
    })

    it('includes the email subject when present', () => {
        const markdown = chatTranscriptMarkdown({ ...ticket, email_subject: 'Page freezes' } as Ticket, [])
        expect(markdown).toContain('- Subject: Page freezes')
    })

    it('preserves markdown in message content verbatim', () => {
        const content = 'Try this:\n\n```js\nposthog.init(token)\n```\n\nAnd **bold** stays bold.'
        const markdown = chatTranscriptMarkdown(ticket, [message({ content })])
        expect(markdown).toContain(content)
    })

    it('serializes rich content when plain content is empty', () => {
        const markdown = chatTranscriptMarkdown(ticket, [
            message({
                content: '',
                richContent: {
                    type: 'doc',
                    content: [
                        {
                            type: 'paragraph',
                            content: [
                                {
                                    type: 'text',
                                    text: 'From rich content',
                                    marks: [{ type: 'bold' }],
                                },
                            ],
                        },
                    ],
                },
            }),
        ])
        expect(markdown).toContain('**From rich content**')
    })

    it('prefers plain content over rich content when both exist', () => {
        const markdown = chatTranscriptMarkdown(ticket, [
            message({
                content: 'Canonical markdown',
                richContent: {
                    type: 'doc',
                    content: [
                        {
                            type: 'paragraph',
                            content: [{ type: 'text', text: 'Rich version' }],
                        },
                    ],
                },
            }),
        ])
        expect(markdown).toContain('Canonical markdown')
        expect(markdown).not.toContain('Rich version')
    })

    it('renders messages without a ticket', () => {
        const markdown = chatTranscriptMarkdown(null, [message({})])
        expect(markdown).toBe('### Brendan Cooper (Customer) · 2026-07-29 17:23 UTC\n\nHello there\n')
    })

    it('converts timestamps to UTC', () => {
        const markdown = chatTranscriptMarkdown(null, [message({ createdAt: '2026-07-29T19:23:00+02:00' })])
        expect(markdown).toContain('· 2026-07-29 17:23 UTC')
    })

    it('uses the human-readable label for snake_case statuses', () => {
        const markdown = chatTranscriptMarkdown({ ...ticket, status: 'on_hold' } as Ticket, [])
        expect(markdown).toContain('- Status: On hold')
    })
})
