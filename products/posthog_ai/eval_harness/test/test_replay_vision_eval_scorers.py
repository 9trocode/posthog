from __future__ import annotations

import random
from typing import Any

import pytest

from products.replay_vision.backend.temporal.types import ScannerSnapshot
from products.replay_vision.evals.collector import order_candidates
from products.replay_vision.evals.dataset import GoldenCase
from products.replay_vision.evals.eval_scanner_quality import build_case
from products.replay_vision.evals.scorers import (
    LabeledOutcome,
    OutputStability,
    ScanCompleted,
    ScoreAlignment,
    SummaryAlignment,
)


def _monitor_output(verdict: str) -> dict[str, Any]:
    return {"model_output": {"verdict": verdict, "reasoning": "r", "confidence": 0.9}}


def _golden(scanner_type: str, label_is_correct: bool | None, recorded_output: dict[str, Any]) -> GoldenCase:
    scanner_config: dict[str, Any] = {"prompt": "watch for rage clicks"}
    if scanner_type == "classifier":
        scanner_config["tags"] = ["a", "b"]
    if scanner_type == "scorer":
        scanner_config["scale"] = {"min": 1, "max": 5}
    return GoldenCase(
        case_id="0199aaaa-0000-7000-8000-000000000001",
        scanner_id="s1",
        scanner_name="Test scanner",
        scanner_type=scanner_type,
        session_id="sess-1",
        team_id=2,
        team_name="Team",
        snapshot=ScannerSnapshot(
            name="Test scanner",
            scanner_type=scanner_type,  # type: ignore[arg-type]
            scanner_version=1,
            model="gemini-3.6-flash",
            provider="google",
            emits_signals=False,
            scanner_config=scanner_config,
        ),
        recorded_output=recorded_output,
        label_is_correct=label_is_correct,
        collected_at="2026-07-30T00:00:00+00:00",
    )


@pytest.mark.parametrize(
    "is_correct,fresh_verdict,expected_score,expected_outcome",
    [
        (True, "yes", 1.0, "kept"),
        (True, "no", 0.0, "regressed"),
        (False, "no", 1.0, "fixed"),
        (False, "yes", 0.0, "still_wrong"),
    ],
)
def test_labeled_outcome_scores_thumbs_semantics(
    is_correct: bool, fresh_verdict: str, expected_score: float, expected_outcome: str
) -> None:
    expected = {"labeled_outcome": {"is_correct": is_correct, "recorded_primary": "Verdict: yes"}}
    score = LabeledOutcome()._run_eval_sync(_monitor_output(fresh_verdict), expected)
    assert score.score == expected_score
    assert score.metadata["outcome"] == expected_outcome


@pytest.mark.parametrize(
    "scorer",
    [LabeledOutcome(), OutputStability(), ScoreAlignment()],
    ids=lambda s: s._name(),
)
def test_inapplicable_cases_skip_instead_of_failing(scorer: Any) -> None:
    # None means "skipped" in the aggregate; returning 0.0 here would silently drag every
    # experiment's mean down for cases the scorer was never meant to grade.
    score = scorer._run_eval_sync(_monitor_output("yes"), expected={})
    assert score.score is None


def test_labeled_outcome_fails_when_scan_produced_nothing() -> None:
    expected = {"labeled_outcome": {"is_correct": True, "recorded_primary": "Verdict: yes"}}
    score = LabeledOutcome()._run_eval_sync({"model_output": None, "error": "boom"}, expected)
    assert score.score == 0.0


@pytest.mark.parametrize(
    "fresh,expected_score",
    [
        (3.0, 1.0),
        (4.0, 0.75),
        (1.0, 0.5),
        (None, 0.0),
    ],
)
def test_score_alignment_normalizes_distance_by_scale(fresh: float | None, expected_score: float) -> None:
    expected = {"score_alignment": {"recorded_score": 3.0, "scale_min": 1.0, "scale_max": 5.0}}
    output = {"model_output": {"score": fresh} if fresh is not None else None}
    score = ScoreAlignment()._run_eval_sync(output, expected)
    assert score.score == pytest.approx(expected_score)


def test_output_stability_compares_primary_outcomes() -> None:
    expected = {"output_stability": {"recorded_primary": "Verdict: yes"}}
    assert OutputStability()._run_eval_sync(_monitor_output("yes"), expected).score == 1.0
    assert OutputStability()._run_eval_sync(_monitor_output("no"), expected).score == 0.0


def test_scan_completed_fails_on_schema_breakage() -> None:
    assert ScanCompleted()._run_eval_sync(_monitor_output("yes"), {}).score == 1.0
    failed = ScanCompleted()._run_eval_sync({"model_output": None, "error": "required step rejected"}, {})
    assert failed.score == 0.0
    assert "rejected" in failed.metadata["reason"]


def test_summary_alignment_prepare_gates_on_reference() -> None:
    judge = SummaryAlignment()
    assert judge._prepare(_monitor_output("yes"), {}).score is None
    spec = {"summary_alignment": {"reference": {"title": "t", "summary": "s"}}}
    assert judge._prepare({"model_output": None}, spec).score == 0.0
    prepared = judge._prepare({"model_output": {"title": "t2", "summary": "s2"}}, spec)
    assert isinstance(prepared, dict)
    assert "t2" in prepared["output"]
    assert "t" in prepared["expected"]


@pytest.mark.parametrize(
    "scanner_type,label_is_correct,recorded_output,expected_key",
    [
        ("monitor", True, {"verdict": "yes"}, "labeled_outcome"),
        ("monitor", None, {"verdict": "yes"}, "output_stability"),
        ("classifier", False, {"tags": ["a"]}, "labeled_outcome"),
        ("scorer", None, {"score": 3.0}, "score_alignment"),
        ("scorer", True, {"score": 3.0}, "score_alignment"),
        ("summarizer", None, {"title": "t", "summary": "s"}, "summary_alignment"),
    ],
)
def test_build_case_routes_to_the_right_scorer(
    scanner_type: str, label_is_correct: bool | None, recorded_output: dict[str, Any], expected_key: str
) -> None:
    case = build_case(_golden(scanner_type, label_is_correct, recorded_output))
    assert list(case.expected.keys()) == [expected_key]


@pytest.mark.parametrize(
    "scanner_type,recorded_output",
    [
        ("scorer", {"score": 3.0}),
        ("summarizer", {"title": "t", "summary": "s"}),
    ],
)
def test_build_case_never_trusts_a_thumbs_downed_reference(scanner_type: str, recorded_output: dict[str, Any]) -> None:
    case = build_case(_golden(scanner_type, False, recorded_output))
    assert case.expected == {}


def test_order_candidates_puts_labeled_first_per_type() -> None:
    def candidate(scanner_type: str, obs_id: str, labeled: bool) -> dict[str, Any]:
        observation: dict[str, Any] = {"id": obs_id, "label": {"is_correct": True} if labeled else None}
        return {"observation": observation, "scanner": {}, "scanner_type": scanner_type}

    candidates = [
        candidate("monitor", "u1", False),
        candidate("monitor", "l1", True),
        candidate("monitor", "u2", False),
        candidate("scorer", "u3", False),
        candidate("monitor", "l2", True),
    ]
    ordered = order_candidates(candidates, random.Random(42))
    monitor_ids = [c["observation"]["id"] for c in ordered["monitor"]]
    assert monitor_ids[:2] == ["l1", "l2"]
    assert sorted(monitor_ids[2:]) == ["u1", "u2"]
    assert [c["observation"]["id"] for c in ordered["scorer"]] == ["u3"]
    assert ordered == order_candidates(candidates, random.Random(42))
