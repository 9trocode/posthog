"""Given a Slack thread and a set of connected repos, does the agent pick the right one?

The last stage of repo selection, and the only expensive one: everything the cascade and
the Haiku gate (`eval_repo_selection.py`) let through arrives here, and each case spends a
sandbox. The agent reads the candidates' descriptions, READMEs, and file trees out of
`system.integration_repository_cache` over MCP, then names one.

`SUITE_KIND` declares SANDBOXED for the infrastructure, not the vehicle: that boots the
sandbox provider, MCP server, gateway, and live server, while the suite drives
`select_repository` through a one-shot task. The sandboxed runner would run the *coding*
agent against a prompt, which is a different agent than the one under test here.

Cases run against the fixture catalogue in `seeders.py` rather than the developer's own
connected repositories, so `expected` can name the repository it should land on. The
predecessor eval could only assert that *some* repo came back — the failure it could not
see was the agent confidently picking the wrong one, which is exactly the failure a user
notices when the agent opens a PR against the marketing site.

To run (expect a few minutes — one sandbox per case):
    hogli evals eval_repo_discovery
    hogli evals eval_repo_discovery --eval sdk_crash --provider modal
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from products.posthog_ai.eval_harness.config import BaseEvalCase
from products.posthog_ai.eval_harness.harness.context import EvalContext
from products.posthog_ai.eval_harness.harness.requirements import SuiteKind
from products.posthog_ai.eval_harness.one_shot import OneShotPublicEval
from products.slack_app.evals.scorers import REPO_SELECTION_KEY, SelectedExpectedRepository, SelectionOutcomeMatch
from products.slack_app.evals.seeders import seed_github_repos

SUITE_KIND = SuiteKind.SANDBOXED


@contextlib.asynccontextmanager
async def _sandbox_slot(ctx: EvalContext) -> AsyncIterator[None]:
    """Hold the run's sandbox limiter, if this run booted one.

    `sandbox_slots` is `None` only when no selected suite required sandbox infrastructure,
    which cannot happen while this suite is selected — but a suite reaching for another
    suite kind's limiter should degrade rather than crash if that ever changes.
    """
    if ctx.sandbox_slots is None:
        yield
        return
    async with ctx.sandbox_slots:
        yield


def _finds(repository: str | None = None) -> dict:
    """The agent is expected to name a repository; `repository=None` grades only that it did.

    No expected stage: every case here is past the cascade and the gate by construction, so
    grading the stage would only confirm this module's own constant. `eval_repo_selection`
    is where the stage genuinely varies.
    """
    spec: dict[str, str] = {"outcome": "found"}
    if repository:
        spec["repository"] = repository
    return {REPO_SELECTION_KEY: spec}


# `prompt` is the mention; `metadata["thread"]` is what the rest of the thread said. The
# agent reads the whole thread, and on several of these the disambiguating evidence is in
# the reply rather than the mention.
CASES = [
    BaseEvalCase(
        name="marketing_site_slow",
        prompt="@PostHog the docs site loads really slowly on mobile",
        metadata={"thread": ["yeah I noticed the same on /docs/getting-started"]},
        expected=_finds("hedgebox/hedgebox-www"),
    ),
    BaseEvalCase(
        name="sdk_crash",
        prompt="@PostHog the app is crashing on launch for iOS users after the 3.19 release",
        metadata={"thread": ["stack trace points at the replay setup during startup"]},
        expected=_finds("hedgebox/hedgebox-ios"),
    ),
    BaseEvalCase(
        name="autocapture_on_own_site",
        prompt="@PostHog autocapture isn't picking up clicks on our checkout button, other buttons work",
        metadata={"thread": ["we just shipped a redesign yesterday"]},
        expected=_finds("hedgebox/hedgebox-web"),
    ),
    BaseEvalCase(
        name="missing_tracking_events",
        prompt="@PostHog we're not seeing any signup_completed events even though we tested signups today",
        metadata={"thread": ["the funnel shows 0 conversions but our team manually completed 5"]},
        expected=_finds("hedgebox/hedgebox-web"),
    ),
    BaseEvalCase(
        name="signup_render_bug",
        prompt="@PostHog there's a bug in how we render user signup, can you fix it",
        expected=_finds("hedgebox/hedgebox-web"),
    ),
    BaseEvalCase(
        name="api_viewset_crash",
        prompt="@PostHog the /api/projects/ viewset crashes on large payloads, can you fix it",
        expected=_finds("hedgebox/hedgebox-api"),
    ),
    BaseEvalCase(
        name="tsx_component_change",
        prompt="@PostHog add a Cancel button to the signup form in the .tsx component",
        expected=_finds("hedgebox/hedgebox-web"),
    ),
    BaseEvalCase(
        name="permission_refactor",
        prompt="@PostHog please refactor the user permission check into a single helper",
        expected=_finds("hedgebox/hedgebox-api"),
    ),
]


async def eval_repo_discovery(ctx: EvalContext) -> None:
    if ctx.demo_data is None:
        raise RuntimeError("eval_repo_discovery requires demo data; check SUITE_KIND")

    # One team for the suite rather than one per case. Every case reads the same fixture
    # catalogue and writes nothing, and a demo clone is a four-table ClickHouse copy plus
    # taxonomy inference — paying that eight times over for identical, unread event data
    # would dominate the suite's runtime. Cloning here also keeps the wait out of each
    # case's own timeout budget, which the one-shot runner starts before the task fn.
    async with ctx.team_setup_slots:
        team = await asyncio.to_thread(ctx.demo_data.make_context, "repo-discovery")
        await asyncio.to_thread(seed_github_repos, team.team_id)

    async def task(case: BaseEvalCase, task_ctx: EvalContext) -> dict:
        # Kept off the module's import path: `harness/discovery.py` imports every
        # `eval_*.py` on every run, including `--list`, and this pulls in products/tasks.
        from products.tasks.backend.facade import api as tasks_facade  # noqa: PLC0415
        from products.tasks.backend.facade.repo_selection import (  # noqa: PLC0415
            RepoSelectionRejectedError,
            RepoSelectionUnavailableError,
            select_repository,
        )

        thread = [case.prompt, *(case.metadata or {}).get("thread", [])]
        context_block = "\n".join(f"tester: {line}" for line in thread)

        try:
            # `select_repository` spends a real sandbox, so it takes the sandbox limiter
            # rather than only the one-shot one — otherwise `--max-sandboxes` is accepted
            # and ignored, and a wide fan-out outruns the memory the operator allowed for.
            async with _sandbox_slot(task_ctx):
                result = await select_repository(
                    team_id=team.team_id,
                    user_id=team.user_id,
                    context=context_block,
                    origin_product=tasks_facade.TaskOriginProduct.SLACK,
                    # Without these the run silently uses the agent-server default while
                    # the experiment is stamped with `ctx.agent_model`.
                    model=task_ctx.agent_model,
                    runtime_adapter=task_ctx.agent_runtime,
                    reasoning_effort=task_ctx.reasoning_effort,
                )
        except RepoSelectionRejectedError as error:
            # The agent named something outside the candidate list. Distinct from "no
            # plausible candidate": the workflow falls back to the picker either way, but
            # only this one means the model made a repository up.
            return {
                "outcome": "rejected",
                "repository": None,
                "detail": f"hallucinated {error.returned_repository}",
                "last_message": f"agent → rejected: {error.returned_repository}",
            }
        except RepoSelectionUnavailableError as error:
            return {
                "outcome": "unavailable",
                "repository": None,
                "detail": str(error),
                "last_message": f"agent → unavailable: {error}",
            }
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            return {"outcome": "error", "error": detail, "last_message": f"agent → error: {detail}"}

        if result.repository is None:
            return {
                "outcome": "no_match",
                "repository": None,
                "detail": result.reason,
                "last_message": f"agent → no_match: {result.reason}",
            }
        return {
            "outcome": "found",
            "repository": result.repository,
            # The reason is the quality signal a score can't carry: it should cite tree
            # paths or README lines, not vibes.
            "detail": result.reason,
            "last_message": f"agent → {result.repository}: {result.reason}",
        }

    await OneShotPublicEval(
        experiment_name="slack-app-repo-discovery",
        cases=CASES,
        scorers=[SelectionOutcomeMatch(), SelectedExpectedRepository()],
        task=task,
        ctx=ctx,
    )
