import { JSONContent } from '@tiptap/core'

import { lemonToast } from '@posthog/lemon-ui'

import { dayjs } from 'lib/dayjs'
import { copyToClipboard } from 'lib/utils/copyToClipboard'
import { addProjectIdIfMissing } from 'lib/utils/kea-router'
import { urls } from 'scenes/urls'

import api, { CountedPaginatedResponse } from '~/lib/api'
import type { CommentType } from '~/types'

import { type ChatMessage, type Ticket, priorityOptions, statusOptionsWithoutAll } from '../../types'
import { serializeToMarkdown } from '../Editor'

const AUTHOR_TYPE_LABELS: Record<ChatMessage['authorType'], string> = {
    customer: 'Customer',
    human: 'Support',
    AI: 'AI',
}

/** Resolve a ticket comment's display identity. Used by both the chat view and the copied transcript. */
export function commentToChatMessage(message: CommentType, ticket: Ticket | null): ChatMessage {
    const authorType = message.item_context?.author_type || 'customer'
    let displayName = 'Anonymous user'
    if (message.created_by) {
        displayName =
            [message.created_by.first_name, message.created_by.last_name].filter(Boolean).join(' ') ||
            message.created_by.email ||
            'Support'
    } else if (authorType === 'AI') {
        displayName = 'PostHog Assistant'
    } else {
        // Per-message author identity (e.g. Zendesk import stores each comment's own
        // author) takes precedence over the ticket-level requester, so a reply from a
        // second requester or an agent shows the real name instead of the ticket owner.
        const messageAuthorName =
            message.item_context?.author_name ||
            message.item_context?.author_email ||
            message.item_context?.slack_author_name ||
            message.item_context?.teams_author_name ||
            message.item_context?.teams_author_email ||
            message.item_context?.email_from_name
        if (messageAuthorName) {
            displayName = messageAuthorName
        } else if (authorType === 'customer') {
            displayName =
                ticket?.person?.properties?.name ||
                ticket?.person?.properties?.email ||
                ticket?.anonymous_traits?.name ||
                ticket?.anonymous_traits?.email ||
                'Anonymous user'
        } else {
            // Staff message with no resolvable author (e.g. deleted ex-agent).
            displayName = 'Support'
        }
    }

    return {
        id: message.id,
        content: message.content || '',
        richContent: message.rich_content,
        authorType: authorType === 'support' ? 'human' : authorType,
        authorName: displayName,
        createdBy: message.created_by,
        createdAt: message.created_at,
        isPrivate: message.item_context?.is_private || false,
        emailDeliveryStatus: message.item_context?.email_delivery_status,
        fromZendesk: message.item_context?.from_zendesk === true,
    }
}

// UTC keeps timestamps unambiguous when the transcript is shared across timezones
function formatTimestamp(isoString: string): string {
    return dayjs(isoString).utc().format('YYYY-MM-DD HH:mm [UTC]')
}

// Header fields are single-line by construction; an author name or subject containing
// line breaks could otherwise fabricate transcript structure (fake headings or rules)
function singleLine(text: string): string {
    return text.replace(/\s+/g, ' ').trim()
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
    const author = `${singleLine(message.authorName)} (${AUTHOR_TYPE_LABELS[message.authorType]})`
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
            ticket.email_subject ? `- Subject: ${singleLine(ticket.email_subject)}` : null,
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

// The comments API is cursor-paginated at 100 per page; follow `next` so long tickets
// are copied in full. Returned oldest first, matching the transcript order.
async function fetchAllTicketMessages(ticketId: string): Promise<CommentType[]> {
    const all: CommentType[] = []
    let response: CountedPaginatedResponse<CommentType> = await api.comments.list({
        scope: 'conversations_ticket',
        item_id: ticketId,
    })
    all.push(...(response.results || []))
    while (response.next) {
        // The cursor URL comes from the API itself; there is no generated client for following it
        // nosemgrep: prefer-codegen-api
        response = await api.get<CountedPaginatedResponse<CommentType>>(response.next)
        all.push(...(response.results || []))
    }
    return all.reverse()
}

/**
 * Copy the whole conversation to the clipboard as markdown. Uses the already-loaded
 * messages when they are complete; refetches every page first when older messages
 * exist beyond what the view has loaded.
 */
export async function copyChatTranscript(
    ticket: Ticket | null,
    loadedMessages: ChatMessage[],
    hasMoreMessages: boolean
): Promise<void> {
    let messages = loadedMessages
    if (ticket && hasMoreMessages) {
        try {
            messages = (await fetchAllTicketMessages(ticket.id)).map((message) => commentToChatMessage(message, ticket))
        } catch {
            lemonToast.error('Failed to load the full conversation, nothing was copied')
            return
        }
    }
    await copyToClipboard(chatTranscriptMarkdown(ticket, messages), 'chat transcript')
}
