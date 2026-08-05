# Desktop announcements admin

Internal tool for publishing PostHog Desktop in-app announcements. It edits the
JSON payload of the `posthog-desktop-announcements` feature flag (project 2)
via the PostHog REST API — nothing else. The release condition / rollout %
stays managed in the PostHog UI.

Live (employee-gated): https://desktop-announcements-admin.hosthog.dev

## Auth

"Log in with PostHog" — an OAuth authorization-code + PKCE flow using a
[client ID metadata document](public/.well-known/oauth-client-metadata.json)
(the `client_id` is that document's URL, so no OAuth app registration and no
API keys). The document is the one public path on the otherwise employee-gated
site; tokens live in `localStorage` with `feature_flag:read/write` scope and
renew silently from the refresh token, so login persists across visits.

The deploy origin is baked into `src/config.ts` and the metadata document, so
the OAuth flow only works on the deployed site — `pnpm dev` renders the UI but
login redirects back to the deployed origin.

## Payload schema

Shared with the app: `@posthog/shared/announcements`
(`packages/shared/src/announcements.ts`). See
`packages/ui/src/features/announcements/AGENTS.md` for how the app consumes it.

## Deploy

```bash
pnpm --filter @posthog/shared build
pnpm --filter @posthog/announcements-admin build
cd tools/announcements-admin/dist && zip -r ../site.zip . && cd -
# publish site.zip to the HostHog slug `desktop-announcements-admin`
# (MCP `publish` tool or `hosthog publish`), then ensure the metadata document
# is ungated: add_public_path /.well-known/oauth-client-metadata.json
```
