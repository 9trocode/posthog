"""In a thread the agent is working in, which untagged replies are meant for it?

Once the Slack App picks up a task, people stop tagging it. They just talk: correcting
scope, pasting a stack trace, answering each other, saying thanks. Every reply in that
thread hits `classify_message_is_agent_directed`, which decides whether the agent is
woken with it or not.

The unit tests around it cover the emoji heuristic and the error path with canned
replies, and would pass with the prompt replaced by "hello". What they cannot cover is
the judgement call: whether a bare stack trace with no addressee is context for the agent
or two humans debugging between themselves.

The errors are not symmetric, which is why `no_dropped_followup` is the number to watch
rather than accuracy. Forwarding side chatter costs one agent turn, in public, where the
thread can correct it. Dropping a real instruction is silent: the agent carries on with
the old understanding and the human only finds out when the result is wrong.

Cases carry a thread so the classifier sees what it sees in production — several are
decidable only from the preceding messages, which is the point.

To run:
    hogli evals eval_followup_classifier
    hogli evals eval_followup_classifier --eval bare_stack_trace
"""

from __future__ import annotations

import asyncio

from posthog.temporal.ai.slack_app.activities.classifiers import classify_message_is_agent_directed

from products.posthog_ai.eval_harness.config import BaseEvalCase
from products.posthog_ai.eval_harness.harness.context import EvalContext
from products.posthog_ai.eval_harness.harness.requirements import SuiteKind
from products.posthog_ai.eval_harness.one_shot import OneShotPublicEval
from products.slack_app.evals.scorers import FOLLOWUP_KEY, FollowupRoutingMatch, NoDroppedFollowup

SUITE_KIND = SuiteKind.ONE_SHOT

TASK_TITLE = "Fix the checkout button not firing autocapture events"

# The thread every case continues: a human asked, the agent acknowledged. Cases add the
# one reply under test on top of it.
THREAD = [
    {"user": "alice", "text": "@PostHog autocapture isn't picking up clicks on our checkout button", "ts": "1.0"},
    {"user": "posthog", "text": "Looking into it — checking how the button is rendered.", "ts": "2.0"},
]


def _routes(agent_directed: bool) -> dict:
    return {FOLLOWUP_KEY: {"agent_directed": agent_directed}}


# Meant for the agent. Only the first one addresses it directly; the rest are the shapes
# people actually use once they have stopped tagging.
DIRECTED_CASES = [
    BaseEvalCase(
        name="direct_instruction",
        prompt="@PostHog also check the mobile breakpoint while you're in there",
        expected=_routes(True),
    ),
    BaseEvalCase(
        name="scope_correction",
        prompt="actually skip the analytics wrapper, just fix the button handler",
        expected=_routes(True),
    ),
    BaseEvalCase(
        name="bare_stack_trace",
        prompt="TypeError: Cannot read properties of undefined (reading 'capture') at CheckoutButton.tsx:42",
        expected=_routes(True),
    ),
    BaseEvalCase(
        name="question_about_the_work",
        prompt="why did you skip the redesign commit? that's when it broke",
        expected=_routes(True),
    ),
    BaseEvalCase(
        name="added_context_no_address",
        prompt="it only happens for logged-out users by the way",
        expected=_routes(True),
    ),
    BaseEvalCase(
        name="file_path_only",
        prompt="src/components/CheckoutButton.tsx",
        expected=_routes(True),
    ),
    BaseEvalCase(
        name="reproduction_steps",
        prompt="repro: open an incognito window, add a file to the cart, click checkout — nothing in the network tab",
        expected=_routes(True),
    ),
]

# Side chatter. The agent should stay asleep; waking it burns a turn on nothing.
CHATTER_CASES = [
    BaseEvalCase(
        name="bare_acknowledgement",
        prompt="thanks!",
        expected=_routes(False),
    ),
    BaseEvalCase(
        name="human_to_human",
        prompt="@bob do you remember why we wrapped the handler in that debounce?",
        expected=_routes(False),
    ),
    BaseEvalCase(
        name="off_topic",
        prompt="lunch in 5?",
        expected=_routes(False),
    ),
    BaseEvalCase(
        name="emoji_only",
        prompt=":tada: :rocket:",
        expected=_routes(False),
    ),
    # Praise about the work is still about the work, but carries nothing to act on —
    # the closest chatter gets to the line without crossing it.
    BaseEvalCase(
        name="approval_without_content",
        prompt="nice, that was quick",
        expected=_routes(False),
    ),
]


async def eval_followup_classifier(ctx: EvalContext) -> None:
    async def task(case: BaseEvalCase, task_ctx: EvalContext) -> dict:
        try:
            # Sync and blocking on the gateway — off the event loop so cases still run
            # concurrently under the harness's limiter.
            agent_directed = await asyncio.to_thread(
                classify_message_is_agent_directed, case.prompt, TASK_TITLE, THREAD
            )
        except Exception as error:
            return {"agent_directed": None, "error": f"{type(error).__name__}: {error}"}
        return {"agent_directed": agent_directed, "last_message": f"agent_directed={agent_directed}"}

    await OneShotPublicEval(
        experiment_name="slack-app-followup-classifier",
        cases=[*DIRECTED_CASES, *CHATTER_CASES],
        scorers=[FollowupRoutingMatch(), NoDroppedFollowup()],
        task=task,
        ctx=ctx,
    )
