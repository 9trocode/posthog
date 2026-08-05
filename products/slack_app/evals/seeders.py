"""Fixture GitHub repositories for the repo-selection suites.

The selection cascade reasons over a team's connected repositories: their names,
descriptions, languages, READMEs, and file trees. The old standalone eval borrowed
whatever the developer's own GitHub account had connected, so a case's result depended on
the machine it ran on and no case could assert *which* repo was right — only that some
repo came back. These fixtures replace that with a fixed six-repo Hedgebox universe, which
is what lets the suites score the chosen repository.

Seeding stays offline by construction. `GitHubRepositoryFullCache.sync_full_cache` reads
`Integration.repository_cache` as its candidate list and skips the network per repo when a
cache row already has a `default_branch_sha` inside the one-hour TTL. Rows written here
satisfy both (`updated_at` is `auto_now`), so the sync is a no-op and nothing reaches
GitHub — no credentials, no rate limit, no flake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.utils import timezone

from posthog.models.integration import Integration
from posthog.models.integration_repository_cache import IntegrationRepositoryCacheEntry


@dataclass(frozen=True)
class RepoFixture:
    """One connected repository, as the selection agent sees it."""

    full_name: str
    description: str
    primary_language: str
    readme: str
    tree_paths: tuple[str, ...]
    topics: tuple[str, ...] = ()
    archived: bool = False


# Six repos spanning the shapes the cases have to tell apart: a marketing/docs site, a
# customer-facing web app, the API behind it, two mobile apps, and infrastructure. The
# distinctions are carried by README and tree evidence rather than by name alone — a
# catalogue the agent can win on name matching would not measure much.
REPOS: tuple[RepoFixture, ...] = (
    RepoFixture(
        full_name="hedgebox/hedgebox-www",
        description="Marketing site and public documentation for Hedgebox",
        primary_language="TypeScript",
        readme=(
            "# hedgebox-www\n\n"
            "The public site: landing pages, pricing, and the documentation at /docs.\n"
            "Built with Next.js and MDX, deployed to the CDN on every merge to main.\n"
            "Page weight and mobile performance are tracked in the Lighthouse workflow.\n"
        ),
        tree_paths=(
            "next.config.js",
            "pages/index.tsx",
            "pages/pricing.tsx",
            "pages/docs/[...slug].tsx",
            "content/docs/getting-started.mdx",
            "content/docs/uploading-files.mdx",
            "components/DocsSidebar.tsx",
            "lighthouserc.json",
        ),
        topics=("marketing", "docs", "nextjs"),
    ),
    RepoFixture(
        full_name="hedgebox/hedgebox-web",
        description="The Hedgebox web app — file browser, sharing, and account settings",
        primary_language="TypeScript",
        readme=(
            "# hedgebox-web\n\n"
            "The signed-in web application: file browser, upload flow, sharing dialogs,\n"
            "signup and checkout. React + TypeScript.\n\n"
            "## Analytics\n\n"
            "PostHog is initialised in `src/analytics/posthog.ts`. Autocapture is on; a few\n"
            "flows send explicit events (`signup_completed`, `file_uploaded`) from\n"
            "`src/analytics/events.ts`.\n"
        ),
        tree_paths=(
            "src/analytics/posthog.ts",
            "src/analytics/events.ts",
            "src/pages/Signup.tsx",
            "src/pages/Checkout.tsx",
            "src/components/CheckoutButton.tsx",
            "src/components/FileBrowser.tsx",
            "src/components/SignupForm.tsx",
            "package.json",
        ),
        topics=("frontend", "react"),
    ),
    RepoFixture(
        full_name="hedgebox/hedgebox-api",
        description="Django REST backend for Hedgebox: accounts, files, billing",
        primary_language="Python",
        readme=(
            "# hedgebox-api\n\n"
            "The Django backend. REST endpoints live under `api/`, one viewset per resource.\n"
            "Permission checks are spread across `api/permissions.py` and the individual\n"
            "viewsets. Billing talks to Stripe from `billing/`.\n"
        ),
        tree_paths=(
            "api/projects/views.py",
            "api/projects/serializers.py",
            "api/files/views.py",
            "api/permissions.py",
            "billing/stripe_client.py",
            "manage.py",
            "requirements.txt",
        ),
        topics=("backend", "django", "api"),
    ),
    RepoFixture(
        full_name="hedgebox/hedgebox-ios",
        description="Hedgebox for iOS",
        primary_language="Swift",
        readme=(
            "# hedgebox-ios\n\n"
            "The iOS app. Ships the PostHog iOS SDK for analytics and session replay;\n"
            "initialisation and replay configuration live in `Hedgebox/Analytics/`.\n"
            "Crash reports come back through the same pipeline.\n"
        ),
        tree_paths=(
            "Hedgebox/AppDelegate.swift",
            "Hedgebox/Analytics/PostHogSetup.swift",
            "Hedgebox/Analytics/ReplayConfig.swift",
            "Hedgebox/Upload/UploadQueue.swift",
            "Podfile",
        ),
        topics=("ios", "swift", "mobile"),
    ),
    RepoFixture(
        full_name="hedgebox/hedgebox-android",
        description="Hedgebox for Android",
        primary_language="Kotlin",
        readme=(
            "# hedgebox-android\n\n"
            "The Android app. Mirrors the iOS feature set; analytics setup lives in\n"
            "`app/src/main/java/com/hedgebox/analytics/`.\n"
        ),
        tree_paths=(
            "app/src/main/java/com/hedgebox/MainActivity.kt",
            "app/src/main/java/com/hedgebox/analytics/PostHogSetup.kt",
            "build.gradle.kts",
        ),
        topics=("android", "kotlin", "mobile"),
    ),
    RepoFixture(
        full_name="hedgebox/hedgebox-infra",
        description="Terraform and Kubernetes manifests for Hedgebox",
        primary_language="HCL",
        readme=(
            "# hedgebox-infra\n\n"
            "Terraform modules and Helm values. No application code — deploys the services\n"
            "the other repositories build.\n"
        ),
        tree_paths=(
            "terraform/prod/main.tf",
            "terraform/modules/rds/main.tf",
            "helm/hedgebox-api/values.yaml",
        ),
        topics=("infrastructure", "terraform"),
    ),
)

REPO_NAMES: tuple[str, ...] = tuple(repo.full_name for repo in REPOS)


def _light_cache_payload() -> list[dict[str, Any]]:
    """The `Integration.repository_cache` snapshot — the candidate list's source of truth.

    `_get_stored_repository_list` drops any entry without an integer `id` and string
    `name`/`full_name`, and an empty result reads as a cache miss that tries to refresh
    from GitHub — so the shape here is what keeps the seed offline.
    """
    return [
        {
            "id": index + 1,
            "name": repo.full_name.split("/", 1)[1],
            "full_name": repo.full_name,
            "private": True,
            "archived": repo.archived,
            "default_branch": "main",
        }
        for index, repo in enumerate(REPOS)
    ]


def seed_github_repos(team_id: int) -> dict[str, Any]:
    """Connect the fixture repositories to `team_id` and return what scorers need.

    Idempotent per team: the light cache is overwritten and each heavy row upserted, so a
    re-run refreshes the TTL rather than duplicating rows.
    """
    integration, _ = Integration.objects.update_or_create(
        team_id=team_id,
        kind="github",
        integration_id="eval-github-fixture",
        defaults={
            "config": {"account": {"type": "Organization", "login": "hedgebox"}},
            "sensitive_config": {},
            "repository_cache": _light_cache_payload(),
            # Inside the light cache's TTL, so `list_all_cached_repositories` serves the
            # seeded snapshot instead of trying to refresh it from GitHub.
            "repository_cache_updated_at": timezone.now(),
            # Empty rather than null (the column is NOT NULL), and never
            # ERROR_TOKEN_REFRESH_FAILED — the resolver skips integrations carrying that.
            "errors": "",
        },
    )

    for repo in REPOS:
        IntegrationRepositoryCacheEntry.objects.update_or_create(
            integration=integration,
            full_name=repo.full_name,
            defaults={
                "team_id": team_id,
                "description": repo.description,
                "topics": list(repo.topics),
                "archived": repo.archived,
                "fork": False,
                "primary_language": repo.primary_language,
                "default_branch": "main",
                # Present and non-empty is what makes the heavy sync take its TTL fast path.
                "default_branch_sha": f"sha-{repo.full_name.replace('/', '-')}",
                "readme": repo.readme,
                "tree_paths": "\n".join(repo.tree_paths),
                "tree_truncated": False,
            },
        )

    return {"integration_id": integration.id, "repositories": list(REPO_NAMES)}
