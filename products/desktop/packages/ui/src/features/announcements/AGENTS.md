# Remote in-app announcements

Broad in-app announcements are **remote content, not code**. They live in the
JSON payload of the `posthog-desktop-announcements` feature flag (PostHog project
2) and render through this feature. Publishing, changing, or retiring an
announcement means editing that flag payload — never adding a component.

**Do not build new ad-hoc announcement surfaces** (flag-gated promo cards,
dismissible banners, one-time modals). That is a Forbidden Pattern in the root
AGENTS.md. `LoopsPromoCard`, `UsageBillingAnnouncementModal`, and
`ScoutAlphaBanner` predate this system and are legacy, not templates.

## How it works

- The payload schema is `packages/shared/src/announcements.ts`
  (`announcementsPayloadSchema`) — shared so authoring tools validate with the
  exact schema the app parses. Items are validated one by one: a malformed
  entry drops alone and is counted, never crashing the batch.
- `selectAnnouncement.ts` is the pure decision function: Zod parse → time
  window (`startsAt`/`endsAt`) → version gate → per-id dismissals → priority.
  It returns nothing when the app version is unknown, which is what keeps the
  web host (no `os.getAppVersion`) announcement-free by construction.
- Dismissals persist per announcement `id` in `announcementsStore.ts`;
  changing an announcement's `id` resurfaces it for everyone.
- Surfaces: `AnnouncementBanner` (top-of-app bar, mounted in `__root.tsx`
  after `ConnectivityBanner`) and `AnnouncementsHost` (the modal surfaces,
  mounted beside `UsageBillingAnnouncementModal`).

## The two kinds

- `kind: "announcement"` — a feature announcement everyone sees,
  `style: "banner" | "modal"`. Optional `minVersion` means "the announced
  feature needs at least this version": apps below it get an "Update now"
  action (`UpdateAction`) in place of the `cta`; apps at or above it get the
  `cta`. Dismissible unless `requiresAck`.
  - `requiresAck: true` (modal only — the schema rejects banners) blocks
    until the user explicitly acts: no dismiss, no Esc. Up-to-date users get
    the ack button (`ackLabel`, default "OK"); users below `minVersion` get
    the update action instead, and **updating counts as acknowledging** — the
    ack records on the update click, so nobody re-sees it after restarting.
- `kind: "required-update"` — shown **only** to apps below its required
  `minVersion`: a blocking, non-dismissible modal (`RequiredUpdateModal`)
  that drives the existing update flow. Users already up to date never see
  it. For "everyone must confirm they saw this" use
  `announcement` + `requiresAck` instead.

## Precedence

One announcement at a time: the first unmet `required-update` in payload
order, else the first eligible `announcement`. While any announcement is
visible the What's New changelog defers (`useAnnouncementVisible` gate in
`WhatsNewModal`), and a blocking announcement suppresses
`UpdateAvailableModal`. The billing announcement predates this system and
wins over both.

## CTA rules

`cta.url` is either `https://…` (opens the default browser) or a
`posthog-code://…` deep link, which dispatches **in-app** via the
`deepLink.open` tRPC forward to the main-process handler — no OS hop, no
browser. Author payloads with the production scheme; `announcementCta.ts`
swaps in the dev scheme on dev builds.

## Testing a payload locally

Dev builds expose `window.posthog` in the renderer devtools:

```js
posthog.featureFlags.overrideFeatureFlags({
  flags: { "posthog-desktop-announcements": true },
  payloads: {
    "posthog-desktop-announcements": {
      announcements: [
        { kind: "announcement", id: "test-1", title: "Hello", body: "It works." },
      ],
    },
  },
});
// clear with: posthog.featureFlags.overrideFeatureFlags(false)
```
