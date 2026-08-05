from __future__ import annotations

from parameterized import parameterized

from products.slack_app.evals.scorers import (
    FOLLOWUP_KEY,
    MODEL_OVERRIDE_KEY,
    REPO_SELECTION_KEY,
    ModelOverrideMatch,
    NoDroppedFollowup,
    NoUnaskedOverride,
    SelectedExpectedRepository,
    SelectionStageMatch,
)

ASKS_FOR_FABLE = {MODEL_OVERRIDE_KEY: {"model": "claude-fable-5", "reasoning_effort": None}}
ASKS_FOR_NOTHING = {MODEL_OVERRIDE_KEY: {"model": None, "reasoning_effort": None}}


def _output(model: str | None = None, effort: str | None = None) -> dict:
    return {"override": {"model": model, "reasoning_effort": effort}}


class TestModelOverrideMatch:
    @parameterized.expand(
        [
            ("both_fields_right", _output("claude-fable-5"), ASKS_FOR_FABLE, 1.0),
            # Right model, wrong effort is still not what the author asked for — scoring
            # the fields independently would report this as a half-success.
            (
                "right_model_wrong_effort",
                _output("claude-fable-5", "max"),
                ASKS_FOR_FABLE,
                0.0,
            ),
            ("wrong_model", _output("claude-opus-5"), ASKS_FOR_FABLE, 0.0),
            ("missed_the_instruction", {"override": None}, ASKS_FOR_FABLE, 0.0),
            # A declined override and an explicit pair of nulls mean the same thing
            # downstream, so they must score the same.
            ("declined_reads_as_nulls", {"override": None}, ASKS_FOR_NOTHING, 1.0),
            ("nulls_read_as_declined", _output(), ASKS_FOR_NOTHING, 1.0),
        ]
    )
    def test_scores(self, _name, output, expected, want):
        assert ModelOverrideMatch().eval(output=output, expected=expected).score == want

    def test_classifier_error_is_a_failure_not_a_skip(self):
        score = ModelOverrideMatch().eval(output={"override": None, "error": "boom"}, expected=ASKS_FOR_FABLE)
        assert score.score == 0.0


class TestNoUnaskedOverride:
    """The suite's headline metric, so its denominator has to be right.

    It reports a rate over mentions that name a model without asking for one. If it ever
    starts scoring the instruction cases too, the rate stays high for the wrong reason —
    diluted by cases that were never at risk of this failure.
    """

    @parameterized.expand(
        [
            ("invented_a_model", _output("claude-fable-5"), 0.0),
            ("invented_an_effort", _output(None, "max"), 0.0),
            ("left_it_alone", _output(), 1.0),
            ("declined", {"override": None}, 1.0),
        ]
    )
    def test_scores_subject_matter_cases(self, _name, output, want):
        assert NoUnaskedOverride().eval(output=output, expected=ASKS_FOR_NOTHING).score == want

    @parameterized.expand(
        [
            ("obeyed", _output("claude-fable-5")),
            ("missed", {"override": None}),
        ]
    )
    def test_skips_cases_that_ask_for_an_override(self, _name, output):
        assert NoUnaskedOverride().eval(output=output, expected=ASKS_FOR_FABLE).score is None

    def test_skips_on_classifier_error(self):
        """An erroring call returns no override, which looks like the safe answer — but
        counting it as one would let a wholly broken classifier post a perfect rate.
        """
        score = NoUnaskedOverride().eval(output={"override": None, "error": "boom"}, expected=ASKS_FOR_NOTHING)
        assert score.score is None


class TestNoDroppedFollowup:
    """The follow-up suite's headline metric, so its denominator has to be right.

    It reports a rate over replies that *were* meant for the agent. Scoring the chatter
    cases too would dilute it with cases that were never at risk of a silent drop.
    """

    @parameterized.expand(
        [
            ("forwarded", {"agent_directed": True}, 1.0),
            ("dropped", {"agent_directed": False}, 0.0),
        ]
    )
    def test_scores_directed_cases(self, _name, output, want):
        expected = {FOLLOWUP_KEY: {"agent_directed": True}}
        assert NoDroppedFollowup().eval(output=output, expected=expected).score == want

    @parameterized.expand([("stayed_asleep", {"agent_directed": False}), ("woke_up", {"agent_directed": True})])
    def test_skips_chatter_cases(self, _name, output):
        expected = {FOLLOWUP_KEY: {"agent_directed": False}}
        assert NoDroppedFollowup().eval(output=output, expected=expected).score is None

    def test_a_failed_call_counts_as_a_drop(self):
        # The classifier returns False on error, which silently drops the message — the
        # exact failure this scorer exists to count, so it must not skip away as infra noise.
        expected = {FOLLOWUP_KEY: {"agent_directed": True}}
        assert (
            NoDroppedFollowup().eval(output={"agent_directed": None, "error": "boom"}, expected=expected).score == 0.0
        )


class TestSelectionStageMatch:
    """Which stage answered is half the result: reaching the right repository through the
    agent when the cascade should have caught it means every such mention now costs a sandbox."""

    @parameterized.expand(
        [
            ("right_stage", {"stage": "cascade", "outcome": "auto"}, 1.0),
            ("escalated_too_far", {"stage": "agent", "outcome": "found"}, 0.0),
        ]
    )
    def test_scores(self, _name, output, want):
        expected = {REPO_SELECTION_KEY: {"stage": "cascade", "outcome": "auto"}}
        assert SelectionStageMatch().eval(output=output, expected=expected).score == want

    def test_skips_when_the_case_declares_no_stage(self):
        assert SelectionStageMatch().eval(output={"stage": "agent"}, expected={}).score is None


class TestSelectedExpectedRepository:
    @parameterized.expand(
        [
            ("picked_it", {"repository": "hedgebox/hedgebox-api"}, 1.0),
            ("picked_another", {"repository": "hedgebox/hedgebox-www"}, 0.0),
            ("picked_nothing", {"repository": None}, 0.0),
        ]
    )
    def test_scores(self, _name, output, want):
        expected = {REPO_SELECTION_KEY: {"stage": "agent", "outcome": "found", "repository": "hedgebox/hedgebox-api"}}
        assert SelectedExpectedRepository().eval(output=output, expected=expected).score == want

    def test_skips_cases_that_never_reach_a_repository(self):
        # The Haiku gate's no-code decisions have no right answer here, and scoring them 0
        # would report the gate working correctly as a selection failure.
        expected = {REPO_SELECTION_KEY: {"stage": "haiku", "outcome": "no_repo"}}
        assert SelectedExpectedRepository().eval(output={"repository": None}, expected=expected).score is None
