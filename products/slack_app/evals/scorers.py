"""Scorers for every Slack app eval suite.

One module rather than one per suite: the suites grade a handful of recurring shapes —
did a classifier read a mention the way a person would, did it invent an instruction
nobody gave — and keeping those side by side is what makes the asymmetry between them
legible. Each scorer opts into a case through its own ``expected`` key and skips
(``score=None``) when that key is absent, so a suite lists only the scorers it wants.
"""

from __future__ import annotations

from typing import Any

from products.posthog_ai.eval_harness.log_parser import LogParser
from products.posthog_ai.eval_harness.scorers import BINARY_CHOICE_SCORES, JUDGE_MODEL, JudgedScorer
from products.posthog_ai.eval_harness.scorers.contract import Score, Scorer

DOCS_SEARCH_TOOL_NAME = "docs-search"


def _user_prompt(output: dict[str, Any] | None) -> str:
    if not output:
        return ""
    raw_log = output.get("raw_log")
    if raw_log:
        return LogParser.cached(raw_log, initial_prompt=output.get("prompt", "") or "").get_user_prompt()
    prompt = output.get("prompt")
    return prompt if isinstance(prompt, str) else ""


EXPECTED_KEY = "posthog_product_answer"


def _answer_under_test(output: dict[str, Any] | None, expected: Any, *, scorer_name: str) -> dict[str, Any] | Score:
    """Shared ``_prepare`` body: pull the capability under test and the final answer.

    Both judges grade the same two strings and opt in through the same ``expected``
    sub-dict, so they apply to a case together or skip together.
    """
    spec = expected.get(EXPECTED_KEY) if isinstance(expected, dict) else None
    if not isinstance(spec, dict) or not spec.get("capability"):
        return Score(name=scorer_name, score=None, metadata={"reason": "Case declares no capability to grade"})
    if not output:
        return Score(name=scorer_name, score=0.0, metadata={"reason": "No output"})
    last_message = output.get("last_message")
    if not isinstance(last_message, str) or not last_message.strip():
        return Score(name=scorer_name, score=0.0, metadata={"reason": "No final assistant message"})
    return {
        "output": {
            "prompt": _user_prompt(output),
            "last_message": last_message,
            "capability": spec["capability"],
        }
    }


class AnsweredAboutPostHogProduct(JudgedScorer):
    """Binary: does the answer explain PostHog's own product for the capability asked about?

    Opts in via ``expected={"posthog_product_answer": {"capability": "..."}}``.
    """

    def _prepare(self, output, expected) -> dict[str, Any] | Score:
        return _answer_under_test(output, expected, scorer_name=self._name())

    def __init__(self, **kwargs):
        super().__init__(
            name="posthog_product_answer",
            prompt_template="""
You are evaluating an answer the PostHog Slack app gave to a customer asking how to set something up.

PostHog ships its own product for the capability below, so the answer should be about that product — what to enable, install, or configure inside PostHog.

Capability the customer asked about:
<capability>
{{output.capability}}
</capability>

Customer's question:
<prompt>
{{output.prompt}}
</prompt>

Agent's final answer:
<final_answer>
{{output.last_message}}
</final_answer>

Does the answer explain how to do this with PostHog's own product? An answer that walks through PostHog setup steps, names the PostHog product or its settings, or links PostHog docs for it qualifies — even if some details are wrong. An answer that only discusses instrumenting the customer's own code, only asks clarifying questions, or is about some other tool does NOT qualify. Answer `yes` or `no`.
""".strip(),
            choice_scores=BINARY_CHOICE_SCORES,
            model=JUDGE_MODEL,
            max_completion_tokens=256,
            **kwargs,
        )


class NoThirdPartyRecommendation(JudgedScorer):
    """Binary: does the answer avoid steering the customer to a non-PostHog product?

    This is the regression that prompted the suite — the agent recommending an
    external tool for a capability PostHog already sells. Opts in via the same
    ``expected={"posthog_product_answer": {"capability": "..."}}`` entry.
    """

    def _prepare(self, output, expected) -> dict[str, Any] | Score:
        return _answer_under_test(output, expected, scorer_name=self._name())

    def __init__(self, **kwargs):
        super().__init__(
            name="no_third_party_recommendation",
            prompt_template="""
You are checking whether an answer from the PostHog Slack app sent a customer to a competitor.

PostHog ships its own product for the capability below, so recommending someone else's product for it is a failure — the customer is already paying for ours.

Capability the customer asked about:
<capability>
{{output.capability}}
</capability>

Customer's question:
<prompt>
{{output.prompt}}
</prompt>

Agent's final answer:
<final_answer>
{{output.last_message}}
</final_answer>

Is the answer free of recommendations to use a non-PostHog product for this capability? Naming a third party PostHog integrates with as a data source or destination is fine, and so is naming one to explain a migration away from it. Suggesting the customer adopt, install, or evaluate someone else's product to get this capability is not. Answer `yes` if the answer is clean, `no` if it steers the customer elsewhere.
""".strip(),
            choice_scores=BINARY_CHOICE_SCORES,
            model=JUDGE_MODEL,
            max_completion_tokens=256,
            **kwargs,
        )


# ---------------------------------------------------------------------------
# Model-override classifier
# ---------------------------------------------------------------------------

# Both scorers read the same expectation: one case states the answer once, and the
# false-positive scorer derives "this case asks for nothing" from it rather than
# carrying a second, separately-maintained flag that could disagree.
MODEL_OVERRIDE_KEY = "model_override_match"


def _reads(output: dict | None) -> tuple[str | None, str | None]:
    """The classifier's answer as a `(model, reasoning_effort)` pair.

    A `None` override — the classifier declining to steer the run — reads the same as an
    explicit pair of nulls, because that is what it means downstream.
    """
    override = (output or {}).get("override") or {}
    return override.get("model"), override.get("reasoning_effort")


class ModelOverrideMatch(Scorer):
    """Did the classifier read the mention the way a person would?

    Scores both fields at once: a run launched on the right model at the wrong effort is
    still not what the author asked for.
    """

    def _name(self) -> str:
        return MODEL_OVERRIDE_KEY

    def _run_eval_sync(self, output: dict | None, expected=None, **kwargs) -> Score:
        if output and output.get("error"):
            return Score(name=self._name(), score=0.0, metadata={"reason": output["error"]})

        want = (expected or {}).get(MODEL_OVERRIDE_KEY)
        if want is None:
            return Score(name=self._name(), score=None, metadata={"reason": "No expectation for this case"})

        got_model, got_effort = _reads(output)
        want_model, want_effort = want.get("model"), want.get("reasoning_effort")
        matched = got_model == want_model and got_effort == want_effort
        return Score(
            name=self._name(),
            score=1.0 if matched else 0.0,
            metadata={
                "expected_model": want_model,
                "actual_model": got_model,
                "expected_effort": want_effort,
                "actual_effort": got_effort,
            },
        )


class NoUnaskedOverride(Scorer):
    """The failure that actually costs the user something.

    The two error directions are not symmetric. Missing a real instruction falls back to
    the author's saved preferences — they notice and rephrase. Inventing an instruction
    out of subject matter silently moves someone's run onto a model they never chose, and
    nothing in the thread says so.

    Skips (`None`) on cases that do ask for something, so the score reads as a rate over
    the mentions that merely *mention* a model.
    """

    def _name(self) -> str:
        return "no_unasked_override"

    def _run_eval_sync(self, output: dict | None, expected=None, **kwargs) -> Score:
        want = (expected or {}).get(MODEL_OVERRIDE_KEY) or {}
        if want.get("model") or want.get("reasoning_effort"):
            return Score(name=self._name(), score=None, metadata={"reason": "Case asks for an override"})

        if output and output.get("error"):
            # An erroring call yields no override, which is the safe answer here — but
            # scoring it as a pass would hide a broken classifier behind a good rate.
            return Score(name=self._name(), score=None, metadata={"reason": output["error"]})

        got_model, got_effort = _reads(output)
        invented = got_model is not None or got_effort is not None
        return Score(
            name=self._name(),
            score=0.0 if invented else 1.0,
            metadata={"actual_model": got_model, "actual_effort": got_effort},
        )


# ---------------------------------------------------------------------------
# Repository selection
# ---------------------------------------------------------------------------

# Selection is a cascade — an explicit repo name short-circuits before any LLM, a Haiku
# gate decides whether code is involved at all, and only what survives both reaches the
# discovery agent. Which stage answered is as much of the result as the answer, because a
# case that reaches the agent has already cost a sandbox.
REPO_SELECTION_KEY = "repo_selection"


class SelectionStageMatch(Scorer):
    """Did selection end at the stage it should have?

    The stage carries the cost: the whole point of the cascade and the Haiku gate is that
    most mentions never pay for the agent. A case that lands on the right repository via
    the wrong stage is a regression in everything but the answer.
    """

    def _name(self) -> str:
        return "selection_stage"

    def _run_eval_sync(self, output: dict | None, expected=None, **kwargs) -> Score:
        want = (expected or {}).get(REPO_SELECTION_KEY)
        if want is None or not want.get("stage"):
            return Score(name=self._name(), score=None, metadata={"reason": "Case declares no expected stage"})
        if output and output.get("error"):
            return Score(name=self._name(), score=0.0, metadata={"reason": output["error"]})

        got = (output or {}).get("stage")
        return Score(
            name=self._name(),
            score=1.0 if got == want["stage"] else 0.0,
            metadata={"expected_stage": want["stage"], "actual_stage": got},
        )


class SelectionOutcomeMatch(Scorer):
    """Did the stage reach the outcome it should have?

    Kept separate from the stage so a failure says which half moved. `no_match`, a
    rejected hallucination, and a crash are all distinct outcomes the workflow handles
    differently downstream, so they are never collapsed into "not found".
    """

    def _name(self) -> str:
        return "selection_outcome"

    def _run_eval_sync(self, output: dict | None, expected=None, **kwargs) -> Score:
        want = (expected or {}).get(REPO_SELECTION_KEY)
        if want is None or not want.get("outcome"):
            return Score(name=self._name(), score=None, metadata={"reason": "Case declares no expected outcome"})
        if output and output.get("error"):
            return Score(name=self._name(), score=0.0, metadata={"reason": output["error"]})

        got = (output or {}).get("outcome")
        return Score(
            name=self._name(),
            score=1.0 if got == want["outcome"] else 0.0,
            metadata={
                "expected_outcome": want["outcome"],
                "actual_outcome": got,
                "detail": (output or {}).get("detail"),
            },
        )


class SelectedExpectedRepository(Scorer):
    """Of the repositories on offer, did it pick the right one?

    Only meaningful against the fixture catalogue in ``seeders.py``: the repositories are
    fixed, so a case can name the answer. Skips on cases that never reach a repository
    (the Haiku gate's no-code decisions) and on cases where more than one repo is a
    defensible read.
    """

    def _name(self) -> str:
        return "selected_expected_repository"

    def _run_eval_sync(self, output: dict | None, expected=None, **kwargs) -> Score:
        want = (expected or {}).get(REPO_SELECTION_KEY) or {}
        wanted_repo = want.get("repository")
        if not wanted_repo:
            return Score(name=self._name(), score=None, metadata={"reason": "Case declares no expected repository"})
        if output and output.get("error"):
            return Score(name=self._name(), score=0.0, metadata={"reason": output["error"]})

        got = (output or {}).get("repository")
        return Score(
            name=self._name(),
            score=1.0 if got == wanted_repo else 0.0,
            metadata={
                "expected_repository": wanted_repo,
                "actual_repository": got,
                "reason": (output or {}).get("detail"),
            },
        )


# ---------------------------------------------------------------------------
# Untagged follow-up routing
# ---------------------------------------------------------------------------

FOLLOWUP_KEY = "followup_routing"


class FollowupRoutingMatch(Scorer):
    """Did the classifier route the reply the way a person in the thread would?"""

    def _name(self) -> str:
        return FOLLOWUP_KEY

    def _run_eval_sync(self, output: dict | None, expected=None, **kwargs) -> Score:
        want = (expected or {}).get(FOLLOWUP_KEY)
        if want is None or "agent_directed" not in want:
            return Score(name=self._name(), score=None, metadata={"reason": "No expectation for this case"})
        if output and output.get("error"):
            return Score(name=self._name(), score=0.0, metadata={"reason": output["error"]})

        got = (output or {}).get("agent_directed")
        return Score(
            name=self._name(),
            score=1.0 if got == want["agent_directed"] else 0.0,
            metadata={"expected": want["agent_directed"], "actual": got},
        )


class NoDroppedFollowup(Scorer):
    """The expensive direction: a real follow-up classified as side chatter.

    The two errors cost differently, and the classifier is deliberately biased to say
    yes because of it. Forwarding chatter wastes one agent turn that the thread can see
    and correct. Dropping an instruction is silent — the agent keeps working on the old
    understanding, and the human only finds out when the result is wrong and has to
    re-tag ``@PostHog`` to recover.

    Skips on cases that are genuinely side chatter, so the score reads as a rate over the
    replies that were actually meant for the agent.
    """

    def _name(self) -> str:
        return "no_dropped_followup"

    def _run_eval_sync(self, output: dict | None, expected=None, **kwargs) -> Score:
        want = (expected or {}).get(FOLLOWUP_KEY) or {}
        if not want.get("agent_directed"):
            return Score(name=self._name(), score=None, metadata={"reason": "Case is side chatter"})
        if output and output.get("error"):
            # A failed call returns False, which drops the message — the failure this
            # scorer exists to count, so it is not skipped away as an infra blip.
            return Score(name=self._name(), score=0.0, metadata={"reason": output["error"]})

        return Score(
            name=self._name(),
            score=1.0 if (output or {}).get("agent_directed") else 0.0,
            metadata={"actual": (output or {}).get("agent_directed")},
        )
