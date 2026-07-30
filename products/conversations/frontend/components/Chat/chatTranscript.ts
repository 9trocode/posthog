import { JSONContent } from '@tiptap/core'

import { dayjs } from 'lib/dayjs'
import { addProjectIdIfMissing } from 'lib/utils/kea-router'
import { urls } from 'scenes/urls'

import { type ChatMessage, type Ticket, priorityOptions, statusOptionsWithoutAll } from '../../types'
import { serializeToMarkdown } from '../Editor'

const AUTHOR_TYPE_LABELS: Record<ChatMessage['authorType'], string> = {
    customer: 'Customer',
    human: 'Support',
    AI: 'AI',
}

// UTC keeps timestamps unambiguous when the transcript is shared across timezones
function formatTimestamp(isoString: string): string {
    return dayjs(isoString).utc().format('YYYY-MM-DD HH:mm [UTC]')
}

function messageBody(message: ChatMessage): string {
    // content is the canonical markdown, written at send/import time; richContent is
    // the rich editor source it was serialized from, kept as a fallback
    const content = message.content?.trim()
    if (content) {
        return content
    }
    return message.richContent ? serializeToMarkdown(message.richContent as JSONContent) : ''
}

function messageSection(message: ChatMessage): string {
    const author = `${message.authorName} (${AUTHOR_TYPE_LABELS[message.authorType]})`
    const privateSuffix = message.isPrivate ? ' (private note)' : ''
    return `### ${author} · ${formatTimestamp(message.createdAt)}${privateSuffix}\n\n${messageBody(message)}`
}

/**
 * Serialize a ticket conversation to a portable CommonMark transcript: a metadata header,
 * then one section per message, separated by horizontal rules so message boundaries stay
 * unambiguous even when a message contains its own headings.
 */
export function chatTranscriptMarkdown(ticket: Ticket | null, messages: ChatMessage[]): string {
    const parts: string[] = []

    if (ticket) {
        const statusLabel = statusOptionsWithoutAll.find((option) => option.value === ticket.status)?.label
        const priorityLabel = priorityOptions.find((option) => option.value === ticket.priority)?.label
        const metadata = [
            ticket.email_subject ? `- Subject: ${ticket.email_subject}` : null,
            `- Channel: ${ticket.channel_source}`,
            statusLabel ? `- Status: ${statusLabel}` : null,
            priorityLabel ? `- Priority: ${priorityLabel}` : null,
            `- Created: ${formatTimestamp(ticket.created_at)}`,
            `- URL: ${window.location.origin}${addProjectIdIfMissing(urls.supportTicketDetail(ticket.ticket_number))}`,
        ].filter(Boolean)
        parts.push(`# Support ticket #${ticket.ticket_number}\n\n${metadata.join('\n')}`)
    }

    parts.push(...messages.map(messageSection))

    return parts.join('\n\n---\n\n').trim() + '\n'
}
