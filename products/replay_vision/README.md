# Replay Vision

A sub-product of Session Replay. Users configure named **scanners** that PostHog applies to completed session recordings; results land as queryable `$recording_observed` events that feed insights, dashboards, and PostHog Signals.

## Concepts

**Scanner** — a configured probe scoped to a team.
Carries a prompt, a scanner type (`monitor` / `classifier` / `scorer` / `summarizer`), a `RecordingsQuery` that selects matching sessions, a Gemini model (which sets the per-observation credit price), and two volume levers: `sampling_mode` (a quality pre-filter over the matched sessions) and `sampling_rate` (a random downsample applied after it).
Each enabled scanner has a Temporal schedule that fires every 5 minutes and sweeps for newly settled recordings past the scanner's watermark (`last_swept_at`); disabling a scanner removes its schedule, and re-enabling restarts the sweep from now rather than backfilling the gap.
Summarizers always emit per-facet embeddings for downstream free-text search.
A scanner with `emits_signals` also pushes one signal per finding into the Signals inbox (`replay_vision` / `scanner_finding`), which is what the editor's Self-driving step turns on.

**Observation** — one application of a scanner to a session, unique per (scanner, session).
Created in `pending` when triggered (by the scanner's schedule, the `/observe/` and `/bulk_observe/` actions, or a retry of a failed observation), transitions to `running` while `ApplyScannerWorkflow` executes (rasterize the recording to video → upload to Gemini → multi-turn scan), and lands in `succeeded` (result persisted under `scanner_result.model_output`, then a `$recording_observed` event plus embeddings/tags emitted fail-soft), `failed` (with a `kind:message` `error_reason`), or `ineligible` (the session doesn't qualify — too short, too idle, no recording).
Each observation snapshots the full scanner state (`scanner_snapshot`) that produced it, so subsequent edits to the scanner don't retro-mutate history.
Rows stranded in `pending`/`running` by a dead workflow are failed as `orphaned` by a reaper on the reconciler tick.
Teams rate observations thumbs up/down, and those ratings drive the scanner's quality view and its AI prompt suggestions.
A finding can also be turned into a PostHog Task once (the observation remembers the task it minted).

**Quota** — succeeded observations write an immutable usage receipt priced in credits (1 credit = $0.01, set by the observation's model).
Usage (receipts + in-flight rows + in-flight prompt tests) counts against the organization's credit limit for the current billing period, falling back to the calendar month when billing hasn't synced the product.
Per-scanner volume estimates are credit-weighted and summed into a projected-spend prognosis shown at configuration time.
Scheduled observations over budget are skipped; on-demand ones are rejected.

## Scenes and tabs

**Scanner list** (`/replay-vision`), two tabs switched through `?tab=`:

| Tab      | `?tab=` | What it shows                                                             |
| -------- | ------- | ------------------------------------------------------------------------- |
| Scanners | (none)  | The team's scanner roster plus the team-wide vision metrics.              |
| Usage    | `usage` | Credit spend over time for the org, bucketed daily/weekly/monthly/yearly. |

**Scanner** (`/replay-vision/<scanner-id>`), six tabs switched through `?tab=`. Overview is the default and writes no param.

| Tab                | `?tab=`         | What it shows                                                                                                                                          |
| ------------------ | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Overview           | `overview`      | At-a-glance panels: impact, verdict mix, top fixed and freeform tags, score distribution. Leads with the daily digest card when vision actions are on. |
| Observations       | `observations`  | The scanner's observations, filterable by status, verdict, tags, and date.                                                                             |
| On-demand          | `on-demand`     | Scan now: by session ID, or by picking from recent recordings.                                                                                         |
| Configuration      | `configuration` | Read-only view of the scanner's current config.                                                                                                        |
| Quality            | `quality`       | Thumbs up/down ratings, accuracy over time, feedback themes, and the AI prompt recommendation with its prompt test.                                    |
| Digests and alerts | `actions`       | The vision actions bound to this scanner. Only rendered behind the `replay-vision-actions` flag.                                                       |

**Scanner editor** (`/replay-vision/<scanner-id>/<step>`) is a stepper rather than tabs: Template, Configure, Scan conditions (`triggers`), Self-driving.
Observations, vision actions, and action runs each have their own scene under `/replay-vision/observations/…` and `/replay-vision/actions/…`.

Outside these scenes, the product also renders inside the session replay player: with the `replay-vision` flag on, `ObservationsDock` replaces the player's summary dock and shows what the team's scanners found about the recording being watched.

## Layout

- `backend/models/` — `ReplayScanner`, `ReplayObservation`, observation labels (ratings), usage receipts, quota grants, prompt suggestions, vision actions.
- `backend/api/` — DRF viewsets and serializers (scanners, observations, prompt suggestions, quota, vision actions, stats, live progress over SSE).
- `backend/queries/` — ClickHouse candidate selection (watermark + settle window + eligibility + sampling) and volume estimates.
- `backend/temporal/` — the apply workflow and its activities, per-scanner sweep, schedule reconciler (+ observation reaper), estimate refresher, prompt evaluation, vision actions, and the Gemini file cleanup sweep.
- `backend/quota.py` + `backend/billing.py` — credit accounting: the per-model price table, the receipt ledger, and the quota snapshot the meter reads.
- `backend/enqueue_claims.py` — atomic slot claims that keep on-demand scans inside the in-flight caps.
- `backend/embeddings.py` — the embedding identity shared by the write and search sides.
- `backend/prompt_suggestions.py` + `backend/proposers/` — rating-driven prompt rewrites, one proposer per scanner type. `backend/prompt_evaluation.py` re-runs a suggestion against rated sessions before it's applied, and `backend/feedback_themes.py` clusters written thumbs-down feedback.
- `backend/impact.py` — affected sessions and users per scanner, exportable as a static cohort.
- `backend/tags.py` + `backend/tag_suggestions.py` — tag slug normalization and data-grounded vocabulary suggestions for classifiers.
- `backend/max_tools.py` — Max AI tools (draft a scanner prompt, digest summaries, semantic search over observations).
- `backend/scanner_access.py` — scanner-level RBAC shared by the API and the vision-action engine.
- `backend/feature_flag.py` — the `replay-vision` and `replay-vision-actions` flag checks + permissions.
- `backend/facade/` — the cross-product entry point (session observations, formatted for Max reports).
- `backend/admin.py` — Django admin registrations.
- `backend/temporal/vision_actions/` + `backend/api/vision_actions.py` — scheduled follow-up actions over observations: group summaries and alerts, including the built-in daily digest, behind the `replay-vision-actions` flag.
- `frontend/` — kea-first scenes and logics for the scanner management UI; `frontend/generated/` carries the generated API types.
