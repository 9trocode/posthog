from __future__ import annotations

from django.test import TestCase

from posthog.models.integration_repository_cache import IntegrationRepositoryCacheEntry
from posthog.models.organization import Organization
from posthog.models.team.team import Team

from products.slack_app.evals.seeders import REPO_NAMES, seed_github_repos
from products.tasks.backend.logic.repo_selection.agent import _list_eligible_full_names, resolve_team_github_integration


class TestEvalRepoSeeder(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="EvalOrg")
        self.team = Team.objects.create(organization=self.org, name="EvalTeam")

    def test_seeded_repos_are_resolvable_and_eligible_without_network(self):
        seed_github_repos(self.team.id)

        github = resolve_team_github_integration(self.team.id)
        assert github is not None
        # A live refresh would need credentials this integration does not have, so reading
        # the seeded snapshot back is also the assertion that no GitHub call was needed.
        assert sorted(entry["full_name"] for entry in github.list_all_cached_repositories()) == sorted(REPO_NAMES)
        assert _list_eligible_full_names(github, self.team.id) == set(REPO_NAMES)

    def test_every_repo_carries_the_evidence_the_agent_selects_on(self):
        seed_github_repos(self.team.id)

        for entry in IntegrationRepositoryCacheEntry.objects.filter(team_id=self.team.id):
            # `default_branch_sha` is what makes the heavy sync take its TTL fast path;
            # without it every eval case would try to fetch the repo from GitHub.
            assert entry.default_branch_sha
            assert entry.readme.strip()
            assert entry.tree_paths.strip()

    def test_reseeding_refreshes_rather_than_duplicates(self):
        seed_github_repos(self.team.id)
        seed_github_repos(self.team.id)

        assert IntegrationRepositoryCacheEntry.objects.filter(team_id=self.team.id).count() == len(REPO_NAMES)
