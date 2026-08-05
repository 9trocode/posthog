from __future__ import annotations

from parameterized import parameterized

from products.slack_app.evals.scorers import (
    FOLLOWUP_KEY,
    MODEL_OVERRIDE_KEY,
    REPO_SELECTION_KEY,
    FollowupRoutingMatch,
    ModelOverrideMatch,
    NoDroppedFollowup,
    NoUnaskedOverride,
    SelectedExpectedRepository,
    SelectionOutcomeMatch,
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


class TestExpectedFieldMatch:
    """The skip/fail protocol every field scorer shares.

    Tested once on the base rather than per subclass — the subclasses declare a field name
    and nothing else, so a per-subclass copy of these cases would only re-test the base.
    The boundary that matters is skip-vs-fail: a scorer that skips where it should fail
    silently inflates its own average.
    """

    @parameterized.expand(
        [
            ("stage_right", SelectionStageMatch, {"stage": "cascade"}, {"stage": "cascade"}, 1.0),
            ("stage_wrong", SelectionStageMatch, {"stage": "agent"}, {"stage": "cascade"}, 0.0),
            ("outcome_right", SelectionOutcomeMatch, {"outcome": "no_repo"}, {"outcome": "no_repo"}, 1.0),
            ("outcome_wrong", SelectionOutcomeMatch, {"outcome": "found"}, {"outcome": "no_repo"}, 0.0),
            (
                "repository_right",
                SelectedExpectedRepository,
                {"repository": "hedgebox/hedgebox-api"},
                {"repository": "hedgebox/hedgebox-api"},
                1.0,
            ),
            (
                "repository_wrong",
                SelectedExpectedRepository,
                {"repository": "hedgebox/hedgebox-www"},
                {"repository": "hedgebox/hedgebox-api"},
                0.0,
            ),
            (
                "repository_none",
                SelectedExpectedRepository,
                {"repository": None},
                {"repository": "hedgebox/hedgebox-api"},
                0.0,
            ),
        ]
    )
    def test_scores(self, _name, scorer_cls, output, want, expected_score):
        expected = {REPO_SELECTION_KEY: want}
        assert scorer_cls().eval(output=output, expected=expected).score == expected_score

    @parameterized.expand(
        [
            # The Haiku gate's no-code decisions have no right repository, and scoring them
            # 0 would report the gate working correctly as a selection failure.
            ("no_repository_expected", SelectedExpectedRepository, {"stage": "haiku", "outcome": "no_repo"}),
            ("no_stage_expected", SelectionStageMatch, {"outcome": "found"}),
            ("nothing_expected", SelectionOutcomeMatch, {}),
        ]
    )
    def test_skips_fields_the_case_does_not_declare(self, _name, scorer_cls, want):
        expected = {REPO_SELECTION_KEY: want}
        assert scorer_cls().eval(output={"stage": "agent", "repository": None}, expected=expected).score is None

    def test_an_expectation_of_false_is_graded_not_skipped(self):
        # Opting in by presence, not truthiness: the follow-up suite's chatter cases expect
        # `False`, and a truthiness check would skip every one of them.
        expected = {FOLLOWUP_KEY: {"agent_directed": False}}
        assert FollowupRoutingMatch().eval(output={"agent_directed": False}, expected=expected).score == 1.0
        assert FollowupRoutingMatch().eval(output={"agent_directed": True}, expected=expected).score == 0.0

    def test_an_errored_call_fails_rather_than_skipping(self):
        expected = {REPO_SELECTION_KEY: {"outcome": "found"}}
        assert SelectionOutcomeMatch().eval(output={"error": "boom"}, expected=expected).score == 0.0
