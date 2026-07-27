---
name: creating-replay-vision-scanners
description: "Guides agents through creating and safely sizing a Replay Vision scanner: choosing the scanner type (monitor/classifier/scorer/summarizer), shaping the RecordingsQuery that selects sessions, and — crucially — estimating the credits it will spend and checking the org's remaining budget before creating, so a broad scanner doesn't exhaust the budget on its first scheduled sweep.\nTRIGGER when: user asks to create, set up, or configure a Replay Vision scanner, OR when you are about to call vision-scanners-create, OR when widening an existing scanner's query, sampling_rate, or sampling_mode (or moving it to a pricier model) via vision-scanners-update.\nDO NOT TRIGGER when: only reading scanners or observations, deleting a scanner, or running an existing scanner against a single session on demand (vision-scanners-scan-session)."
---

# Creating Replay Vision scanners

A scanner is a standing LLM probe over session recordings. Once created and enabled, it runs on a
**Temporal schedule that sweeps every 5 minutes**, applying its prompt to each new matching recording and
recording the result as an observation (a queryable `$recording_observed` event). Each observation spends
**credits** (1 credit = $0.01) from the org's budget for the current billing period, and how many depends on
the scanner's `model`.

That schedule is exactly why creation needs a gut-check: a scanner with a permissive query and full sampling
starts spending automatically and can drain the whole period's budget within its first few sweeps.
Creation itself does **not** check quota — that protection only kicks in at observation time, by which point
the budget may already be gone.

## Core principle: size before you ship

Never create an enabled scanner blind. Estimate its volume, check remaining quota, and — when the projected
volume is a meaningful fraction of what's left — show the user the numbers and get confirmation before
creating. This is the heart of the skill; the rest is supporting detail.

## The flow

### Step 1: What should the scanner do?

Pick a `scanner_type` and write its `scanner_config`. Every type needs a `prompt`; the rest is type-specific:

| Type         | What it produces                                                  | `scanner_config` shape                                                                                                                                                      |
| ------------ | ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `monitor`    | Open-ended observation against a prompt (e.g. "flag rage clicks") | `{"prompt": "..."}`; optional `"allow_inconclusive": true` (off by default, so the model must answer yes or no)                                                             |
| `classifier` | Assigns tags from a fixed label set                               | `{"prompt": "...", "tags": ["tag-a", "tag-b"]}` — `tags` needs ≥1 entry; optional `"multi_label": false` (defaults to true), `"allow_freeform_tags": true` (off by default) |
| `scorer`     | Numeric score on a rubric                                         | `{"prompt": "...", "scale": {"min": 1, "max": 5, "label": "frustration"}}` — `min` < `max`; `label` optional                                                                |
| `summarizer` | Free-text summary, plus facet embeddings for search               | `{"prompt": "..."}`; optional `"length": "short" \| "medium" \| "long"` (default `"medium"`). Embeddings are always on                                                      |

`scanner_type` is **locked after creation** — to change it you delete and recreate, so confirm the type is
right up front, and get the `scanner_config` shape right (a wrong shape is a create error, not a silent
default).

If the user's intent makes the type and prompt obvious, just proceed — don't interrogate them.

### Step 2: Which sessions?

The `query` is a `RecordingsQuery` shape that selects which recordings the scanner watches. `date_from` and
`date_to` are **ignored** (the schedule controls time), so don't bother setting them. Narrow the query to the
sessions that actually matter — by event, URL, person property, duration, etc. A narrow query is the single
biggest lever on cost.

Two levers narrow it further, applied in this order:

- `sampling_mode` (default `comprehensive`) is a quality pre-filter on the matched sessions: `focused` keeps
  only the top sessions by surfacing score, `balanced` drops the lowest-quality ones, `comprehensive` keeps
  everything. Use it to spend the budget on sessions worth watching rather than shrinking coverage at random.
- `sampling_rate` (0..1, default 1.0) is a random downsample applied after that. Lower it to trade coverage
  for budget. Exactly 0 pauses scanning; non-zero rates below 0.0001 are rejected.

#### Which model?

`model` sets the price of every observation the scanner makes, so it's a cost lever as much as a quality one:
`gemini-3.5-flash-lite` (2 credits), `gemini-3-flash-preview` (5 credits, the default) and `gemini-3.6-flash`
(15 credits). Start at the default and only reach for `gemini-3.6-flash` when the cheaper tiers demonstrably
miss what the scanner is looking for.

### Step 3: Size it — the gut-check (do not skip)

Before creating, run both checks and reason about them together:

1. **Estimate spend** — call `vision-scanners-estimate-create` with the proposed `query`, `sampling_rate`,
   `sampling_mode` and `model`. It returns `matched_sessions_in_window`, the `window_days` measured,
   `estimated_observations_per_month`, `credits_per_observation`, `estimated_credits_per_month`, and
   `other_enabled_scanners_monthly_credits` (what the org's other enabled scanners are already projected to
   spend). When editing an existing scanner, pass its `scanner_id` so its own estimate isn't counted twice.
2. **Check budget** — call `vision-quota-retrieve` for `remaining` and `exhausted` against the org's
   `credit_limit` (credits, 1 credit = $0.01; `null` when uncapped).

Compare credits with credits: `estimated_credits_per_month` plus `other_enabled_scanners_monthly_credits`
against `remaining`. Then decide:

- If the projection comfortably fits within `remaining`, proceed.
- If it's a large fraction of (or exceeds) `remaining`, **stop and tell the user the concrete numbers**
  (e.g. "This scanner is projected to spend ~X credits/month, about $Y; you have Z left this period."), then
  confirm before creating. Tightening the `query`, switching `sampling_mode` to `focused`, lowering
  `sampling_rate`, or picking a cheaper `model` are all ways to bring it down.
- If the org is already `exhausted`, say so. A new enabled scanner won't produce anything until the budget
  resets: its scheduled observations are silently skipped, and on-demand scans are rejected outright.

Confirmation here is a conversation step, not an API capability — surface the trade-off and let the user
choose. When the projected volume is clearly small relative to the budget, you don't need to ask.

### Step 4: Create

Call `vision-scanners-create`. Minimal example:

```json
{
  "name": "Rage click monitor",
  "scanner_type": "monitor",
  "scanner_config": { "prompt": "Flag sessions where the user repeatedly clicks the same element in frustration." },
  "query": { "kind": "RecordingsQuery", "events": [{ "id": "$rageclick", "type": "events" }] },
  "sampling_rate": 1.0,
  "sampling_mode": "comprehensive",
  "model": "gemini-3-flash-preview",
  "enabled": true
}
```

`name` must be unique within the team. Set `enabled: false` if the user wants to create it paused (no
schedule, no quota consumption) and turn it on later.

## After creation

- Show the scanner's PostHog URL from the response so the user can review it in the UI.
- Results take a few minutes to appear (rasterizing the recording to video + the LLM call are slow). Inspect
  them with `vision-scanners-observations-list` for one scanner over time, or `vision-observations-list`
  (requires `session_id`) for every scanner's findings on a single session. To dig into a recording, hand off
  to the `investigating-replay` skill.

## Updating an existing scanner

`vision-scanners-update` is a partial update — send only changed fields. **Re-run the Step 3 gut-check
whenever you widen scope or raise the price**: a broader `query`, a higher `sampling_rate`, a looser
`sampling_mode`, or a pricier `model` all raise the monthly spend just like a fresh broad scanner would.
Toggling `enabled`, tweaking the prompt, or narrowing the query don't need a re-estimate. Editing config bumps
`scanner_version`; past observations keep a snapshot of the old config.

## Gotchas

- **One observation per (scanner, session).** Re-running a scanner on a session it already observed — even a
  failed or ineligible one — is a no-op and won't produce a fresh scan. A failed observation can be retried
  from the UI (which replaces it), but there's no MCP tool for that.
- **Ineligible ≠ failed.** Observations can land `ineligible` (e.g. `too_short`, `no_recording`) — a terminal
  non-error outcome. Check `error_reason` when triaging why a scanner produced nothing.
- **Provider/model are Google/Gemini only** in the current version.
