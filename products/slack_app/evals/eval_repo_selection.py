"""Where does a Slack mention exit the repo-selection cascade?

Selection runs in three stages, cheapest first: a synchronous cascade that short-circuits
when the text names a connected repo, a Haiku gate deciding whether code is involved at
all, and — only for what survives both — the sandbox-backed discovery agent. This suite
covers the two cheap stages; `eval_repo_discovery.py` covers the agent.

The stages are split across two suites because their scorecards are: nothing here spends
a sandbox, so it runs in seconds and can be re-run on every prompt tweak, while the agent
suite costs a sandbox per case. Keeping them together would put a two-second check behind
a two-minute one.

What the cheap stages have to get right is asymmetric, and it is the Haiku gate that
carries the risk. A false negative answers an analytics question with no repo attached
and the author re-asks; a false positive walls "what's my DAU" behind the Connect-GitHub
gate, which they cannot talk their way past. `classify_task_needs_repo` is biased toward
False for that reason, and these cases are chosen to hold that bias in place.

To run:
    hogli evals eval_repo_selection
    hogli evals eval_repo_selection --eval billing_question
"""

from __future__ import annotations

import asyncio

from posthog.temporal.ai.slack_app.activities.classifiers import classify_task_needs_repo

from products.posthog_ai.eval_harness.config import BaseEvalCase
from products.posthog_ai.eval_harness.harness.context import EvalContext
from products.posthog_ai.eval_harness.harness.requirements import SuiteKind
from products.posthog_ai.eval_harness.one_shot import OneShotPublicEval
from products.slack_app.evals.scorers import REPO_SELECTION_KEY, SelectionOutcomeMatch, SelectionStageMatch
from products.slack_app.evals.seeders import REPO_NAMES

SUITE_KIND = SuiteKind.ONE_SHOT

# The cascade matches against the team's connected repositories; these suites run against
# the fixture catalogue rather than whatever the developer has connected.
EXPLICIT_REPO = REPO_NAMES[0]


def _ends_at(stage: str, outcome: str) -> dict:
    return {REPO_SELECTION_KEY: {"stage": stage, "outcome": outcome}}


CASES = [
    # --- Cascade: named outright, so no model is asked anything ----------------
    BaseEvalCase(
        name="explicit_mention",
        prompt=f"@PostHog can you look at {EXPLICIT_REPO} and fix the readme typo",
        expected=_ends_at("cascade", "auto"),
    ),
    # --- Haiku gate: no code involved, so selection stops here -----------------
    BaseEvalCase(
        name="billing_question",
        prompt="@PostHog how do I update the credit card on our subscription?",
        expected=_ends_at("haiku", "no_repo"),
    ),
    BaseEvalCase(
        name="dashboard_config",
        prompt="@PostHog the dashboard tile filters are not persisting across refreshes",
        expected=_ends_at("haiku", "no_repo"),
    ),
    # A complaint about PostHog's own product reads like "something is broken", but the
    # broken thing is the SaaS, not the customer's code — no repo of theirs can fix it.
    BaseEvalCase(
        name="posthog_product_hang",
        prompt="@PostHog the trends page hangs forever when I add 10+ series",
        expected=_ends_at("haiku", "no_repo"),
    ),
    # --- Reaches the agent: the gate's job here is to get out of the way -------
    # Scored as `needs_agent` rather than for a repository; which repo comes back is
    # `eval_repo_discovery.py`'s question.
    BaseEvalCase(
        name="api_viewset",
        prompt="@PostHog the /api/projects/ viewset crashes on large payloads, can you fix it",
        expected=_ends_at("haiku", "needs_agent"),
    ),
    BaseEvalCase(
        name="explicit_code_file",
        prompt="@PostHog add a Cancel button to the signup form in the .tsx component",
        expected=_ends_at("haiku", "needs_agent"),
    ),
    BaseEvalCase(
        name="refactor_request",
        prompt="@PostHog please refactor the user permission check into a single helper",
        expected=_ends_at("haiku", "needs_agent"),
    ),
    BaseEvalCase(
        name="wrong_data_tracking_bug",
        prompt="@PostHog we're not seeing any signup_completed events even though we tested signups today",
        expected=_ends_at("haiku", "needs_agent"),
    ),
]


def _extract_explicit_repo(text: str, candidates: tuple[str, ...]) -> str | None:
    """The cascade's short-circuit: does the text name a connected repo outright?

    Mirrors the production cascade's matching on both the full `owner/repo` and the bare
    repo name, which is how people actually write them in Slack.
    """
    lowered = text.lower()
    for full_name in candidates:
        if full_name.lower() in lowered:
            return full_name
        if full_name.split("/", 1)[1].lower() in lowered:
            return full_name
    return None


async def eval_repo_selection(ctx: EvalContext) -> None:
    async def task(case: BaseEvalCase, task_ctx: EvalContext) -> dict:
        explicit = _extract_explicit_repo(case.prompt, REPO_NAMES)
        if explicit:
            return {
                "stage": "cascade",
                "outcome": "auto",
                "repository": explicit,
                "detail": explicit,
                "last_message": f"cascade → {explicit}",
            }

        thread_messages = [{"user": "tester", "text": case.prompt}]
        try:
            # Sync and blocking on the gateway — off the event loop so cases still run
            # concurrently under the harness's limiter.
            needs_repo = await asyncio.to_thread(classify_task_needs_repo, case.prompt, thread_messages)
        except Exception as error:
            return {"stage": "haiku", "outcome": "error", "error": f"{type(error).__name__}: {error}"}

        outcome = "needs_agent" if needs_repo else "no_repo"
        return {
            "stage": "haiku",
            "outcome": outcome,
            "repository": None,
            "detail": None,
            "last_message": f"haiku → {outcome}",
        }

    await OneShotPublicEval(
        experiment_name="slack-app-repo-selection",
        cases=CASES,
        scorers=[SelectionStageMatch(), SelectionOutcomeMatch()],
        task=task,
        ctx=ctx,
    )
