# Copyright (c) 2026 Jonas Thelemann
"""Unit tests for main module."""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING

import pytest
import responses

from src.main import (
    CACHE_TTL_SECONDS,
    BatchTooLargeError,
    GitHubGraphQlError,
    MissingTokenError,
    UnknownUserError,
    _build_contribution,
    _build_repository,
    build_batch_query,
    cache_path,
    cache_read,
    cache_write,
    discover_contributed_repos,
    enrich_batch,
    extract_pinned_repos,
    fetch_user_id,
    github_graphql,
    read_json_seed,
    read_pins_seed,
)
from tests.conftest import GRAPHQL_URL

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.mark.unit
class TestCachePath:
    """Tests for cache_path function."""

    def test_is_stable_per_key(self) -> None:
        """Test that the same key always maps to the same file."""
        assert cache_path("contribution:nuxt/nuxt") == cache_path(
            "contribution:nuxt/nuxt"
        )

    def test_differs_per_key(self) -> None:
        """Test that different keys map to different files."""
        assert cache_path("a") != cache_path("b")


@pytest.mark.unit
class TestCache:
    """Tests for cache_read and cache_write."""

    def test_read_missing_entry(self) -> None:
        """Test that an absent entry reads as None."""
        assert cache_read("absent") is None

    def test_round_trip(self) -> None:
        """Test that a written entry reads back, directory creation included."""
        cache_write("key", {"a": 1})

        assert cache_read("key") == {"a": 1}

    def test_read_expired_entry(self) -> None:
        """Test that an entry older than the TTL reads as None."""
        cache_write("key", {"a": 1})
        stale = time.time() - CACHE_TTL_SECONDS - 1
        os.utime(cache_path("key"), (stale, stale))

        assert cache_read("key") is None


@pytest.mark.unit
class TestGithubGraphql:
    """Tests for github_graphql function."""

    def test_requires_a_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that an empty token fails before any request is made."""
        monkeypatch.setattr("src.main.GITHUB_TOKEN", "")

        with pytest.raises(MissingTokenError):
            github_graphql("query { viewer { id } }", {})

    @responses.activate
    def test_sends_the_bearer_token(self) -> None:
        """Test that the configured token is sent as a bearer token."""
        responses.post(GRAPHQL_URL, json={"data": {"ok": True}})

        github_graphql("query { viewer { id } }", {})

        assert (
            responses.calls[0].request.headers["Authorization"]
            == "bearer test_token_12345"
        )

    @responses.activate
    def test_bad_gateway_asks_for_a_smaller_batch(self) -> None:
        """Test that HTTP 502 is reported as a batch that has to be split."""
        responses.post(GRAPHQL_URL, status=502)

        with pytest.raises(BatchTooLargeError):
            github_graphql("query { viewer { id } }", {})

    @responses.activate
    @pytest.mark.parametrize("status", [401, 500])
    def test_other_failures_raise(self, status: int) -> None:
        """Test that any other error status raises."""
        responses.post(GRAPHQL_URL, status=status, json={})

        with pytest.raises(GitHubGraphQlError):
            github_graphql("query { viewer { id } }", {})

    @responses.activate
    def test_partial_response_is_kept(self) -> None:
        """Test that data alongside errors is returned rather than discarded.

        One inaccessible repository in a batch should not lose the other seven.
        """
        responses.post(
            GRAPHQL_URL,
            json={"data": {"repo0": None}, "errors": [{"message": "Not found"}]},
        )

        assert github_graphql("query { viewer { id } }", {}) == {"repo0": None}

    @responses.activate
    def test_errors_without_data_raise(self) -> None:
        """Test that a response carrying only errors raises."""
        responses.post(GRAPHQL_URL, json={"errors": [{"message": "Bad credentials"}]})

        with pytest.raises(GitHubGraphQlError):
            github_graphql("query { viewer { id } }", {})


@pytest.mark.unit
class TestFetchUserId:
    """Tests for fetch_user_id function."""

    @responses.activate
    def test_returns_the_node_id(self) -> None:
        """Test that the user's node ID is returned."""
        responses.post(GRAPHQL_URL, json={"data": {"user": {"id": "MDQ6VXNlcjQ="}}})

        assert fetch_user_id("testuser") == "MDQ6VXNlcjQ="

    @responses.activate
    def test_unknown_user_raises(self) -> None:
        """Test that a missing user raises."""
        responses.post(GRAPHQL_URL, json={"data": {"user": None}})

        with pytest.raises(UnknownUserError):
            fetch_user_id("testuser")


@pytest.mark.unit
class TestDiscoverContributedRepos:
    """Tests for discover_contributed_repos function."""

    @staticmethod
    def _page(
        nodes: list[dict[str, object]],
        *,
        has_next: bool = False,
    ) -> dict[str, object]:
        """Build one page of the contributions connection."""
        return {
            "data": {
                "user": {
                    "repositoriesContributedTo": {
                        "nodes": nodes,
                        "pageInfo": {"endCursor": "cursor", "hasNextPage": has_next},
                    },
                },
            },
        }

    @responses.activate
    def test_follows_pagination(self) -> None:
        """Test that every page is collected."""
        responses.post(
            GRAPHQL_URL,
            json=self._page(
                [{"isFork": False, "nameWithOwner": "nuxt/nuxt"}],
                has_next=True,
            ),
        )
        responses.post(
            GRAPHQL_URL,
            json=self._page([{"isFork": False, "nameWithOwner": "unjs/nitro"}]),
        )

        assert discover_contributed_repos("testuser") == {"nuxt/nuxt", "unjs/nitro"}

    @responses.activate
    def test_excludes_forks(self) -> None:
        """Test that forks are left out.

        A branch pushed to your own fork is not a contribution to a project.
        """
        responses.post(
            GRAPHQL_URL,
            json=self._page(
                [
                    {"isFork": True, "nameWithOwner": "testuser/nuxt"},
                    {"isFork": False, "nameWithOwner": "nuxt/nuxt"},
                ],
            ),
        )

        assert discover_contributed_repos("testuser") == {"nuxt/nuxt"}

    @responses.activate
    def test_unknown_user_raises(self) -> None:
        """Test that a missing user raises."""
        responses.post(GRAPHQL_URL, json={"data": {"user": None}})

        with pytest.raises(UnknownUserError):
            discover_contributed_repos("testuser")

    @responses.activate
    def test_missing_connection_ends_the_walk(self) -> None:
        """Test that a response without the connection stops rather than looping."""
        responses.post(GRAPHQL_URL, json={"data": {"user": {}}})

        assert discover_contributed_repos("testuser") == set()


@pytest.mark.unit
class TestExtractPinnedRepos:
    """Tests for extract_pinned_repos function."""

    def test_extracts_and_prefixes(self, sample_html: str) -> None:
        """Test that owned repositories are prefixed and gists are ignored."""
        assert extract_pinned_repos(sample_html, "testuser") == {
            "nuxt/nuxt",
            "testuser/test-repo",
        }

    def test_ignores_markup_without_repositories(self) -> None:
        """Test that unrelated markup yields nothing."""
        assert extract_pinned_repos("<ul></ul>", "testuser") == set()

    def test_ignores_an_entry_without_a_name(self) -> None:
        """Test that a pin row carrying no repository name is skipped."""
        html = "<li class='source' data-pinnable-type='repository'></li>"

        assert extract_pinned_repos(html, "testuser") == set()


@pytest.mark.unit
class TestSeeds:
    """Tests for the optional seed inputs."""

    def test_pins_seed_is_optional(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Test that a missing pins export is not an error."""
        monkeypatch.chdir(tmp_path)

        assert read_pins_seed("testuser") == set()

    def test_pins_seed_is_read(
        self,
        monkeypatch: pytest.MonkeyPatch,
        sample_html: str,
        tmp_path: Path,
    ) -> None:
        """Test that a present pins export is parsed."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "input").mkdir()
        (tmp_path / "input/profile-pins.html").write_text(sample_html)

        assert read_pins_seed("testuser") == {"nuxt/nuxt", "testuser/test-repo"}

    def test_json_seed_is_optional(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Test that a missing JSON seed is not an error."""
        monkeypatch.chdir(tmp_path)

        assert read_json_seed() == set()

    def test_json_seed_reads_slugs_of_a_previous_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Test that a previous output is read back as a set of slugs."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "input").mkdir()
        (tmp_path / "input/seed.json").write_text(
            json.dumps(
                [
                    {"repository": {"name": "nuxt", "owner": {"name": "nuxt"}}},
                    {"repository": {"owner": {"name": "broken"}}},
                ],
            ),
        )

        assert read_json_seed() == {"nuxt/nuxt"}


@pytest.mark.unit
class TestBuildBatchQuery:
    """Tests for build_batch_query function."""

    def test_aliases_every_repository(self) -> None:
        """Test that each repository gets its own set of aliases."""
        query = build_batch_query(["nuxt/nuxt", "unjs/nitro"], "testuser")

        for alias in ("repo0", "merged0", "reviews0", "issues0", "repo1"):
            assert f"{alias}:" in query

    def test_declares_the_author_variable(self) -> None:
        """Test that the commit author is passed as a variable."""
        query = build_batch_query(["nuxt/nuxt"], "testuser")

        assert query.startswith("query($userId: ID!)")
        assert "author: {id: $userId}" in query

    def test_embeds_the_slug_and_login(self) -> None:
        """Test that the searches are scoped to the repository and the user."""
        query = build_batch_query(["nuxt/nuxt"], "testuser")

        assert 'repository(owner: "nuxt", name: "nuxt")' in query
        assert "repo:nuxt/nuxt author:testuser type:pr is:merged" in query
        assert "repo:nuxt/nuxt reviewed-by:testuser type:pr" in query


@pytest.mark.unit
class TestBuildRepository:
    """Tests for _build_repository function."""

    def test_maps_every_field(
        self,
        repo_node: Callable[..., dict[str, object]],
    ) -> None:
        """Test that the GraphQL names are mapped onto the output names."""
        assert _build_repository(repo_node()) == {
            "description": "The full-stack Vue framework.",
            "fork": False,
            "name": "nuxt",
            "owner": {
                "avatar_url": "https://avatars.githubusercontent.com/nuxt",
                "name": "nuxt",
                "type": "Organization",
                "url": "https://github.com/nuxt",
            },
            "stars": 60872,
            "url": "https://github.com/nuxt/nuxt",
        }

    def test_missing_fields_become_none(self) -> None:
        """Test that an empty response does not raise."""
        repository = _build_repository({})

        assert repository["name"] is None
        assert repository["stars"] is None
        assert repository["owner"]["name"] is None


@pytest.mark.unit
class TestBuildContribution:
    """Tests for _build_contribution function."""

    def test_maps_counts_and_role(
        self,
        repo_node: Callable[..., dict[str, object]],
    ) -> None:
        """Test that the counts and the permission are carried over."""
        contribution = _build_contribution(
            repo_node(),
            {"issueCount": 6, "nodes": [{"mergedAt": "2026-01-29T09:39:31Z"}]},
            {"issueCount": 5},
            {"issueCount": 15},
        )

        assert contribution == {
            "commits": 6,
            "issues": 15,
            "last_activity_at": "2026-01-29",
            "pull_requests_merged": 6,
            "reviews": 5,
            "role": "TRIAGE",
        }

    def test_last_activity_takes_the_newer_date(
        self,
        repo_node: Callable[..., dict[str, object]],
    ) -> None:
        """Test that a later merge outranks an earlier commit."""
        contribution = _build_contribution(
            repo_node(),
            {"issueCount": 1, "nodes": [{"mergedAt": "2026-06-01T00:00:00Z"}]},
            {"issueCount": 0},
            {"issueCount": 0},
        )

        assert contribution["last_activity_at"] == "2026-06-01"

    def test_last_activity_survives_a_missing_commit(
        self,
        repo_node: Callable[..., dict[str, object]],
    ) -> None:
        """Test that a merge alone still dates the contribution.

        Someone else landing the work leaves no commit of one's own behind.
        """
        contribution = _build_contribution(
            repo_node(defaultBranchRef=None),
            {"issueCount": 1, "nodes": [{"mergedAt": "2026-06-01T00:00:00Z"}]},
            {"issueCount": 0},
            {"issueCount": 0},
        )

        assert contribution["commits"] == 0
        assert contribution["last_activity_at"] == "2026-06-01"

    def test_empty_response_is_all_zeroes(self) -> None:
        """Test that a repository with no involvement reports zeroes."""
        assert _build_contribution({}, None, None, None) == {
            "commits": 0,
            "issues": 0,
            "last_activity_at": None,
            "pull_requests_merged": 0,
            "reviews": 0,
            "role": None,
        }


@pytest.mark.unit
class TestEnrichBatch:
    """Tests for enrich_batch function."""

    @responses.activate
    def test_maps_a_response_onto_slugs(
        self,
        batch_response: Callable[..., dict[str, object]],
    ) -> None:
        """Test that a batch comes back keyed by repository slug."""
        responses.post(GRAPHQL_URL, json={"data": batch_response()})

        enriched = enrich_batch(["nuxt/nuxt"], "testuser", "MDQ6VXNlcjQ=")

        assert list(enriched) == ["nuxt/nuxt"]
        assert enriched["nuxt/nuxt"]["contribution"]["commits"] == 6
        assert enriched["nuxt/nuxt"]["repository"]["stars"] == 60872

    @responses.activate
    def test_follows_a_rename(
        self,
        batch_response: Callable[..., dict[str, object]],
    ) -> None:
        """Test that a renamed repository lands under the name GitHub reports."""
        responses.post(GRAPHQL_URL, json={"data": batch_response(slug="nuxt/nuxt")})

        enriched = enrich_batch(["nuxt/framework"], "testuser", "MDQ6VXNlcjQ=")

        assert list(enriched) == ["nuxt/nuxt"]

    @responses.activate
    def test_skips_an_inaccessible_repository(self) -> None:
        """Test that a null repository is dropped rather than crashing the batch."""
        responses.post(GRAPHQL_URL, json={"data": {"repo0": None}})

        assert enrich_batch(["nuxt/gone"], "testuser", "MDQ6VXNlcjQ=") == {}

    @responses.activate
    def test_splits_a_batch_it_cannot_answer(
        self,
        batch_response: Callable[..., dict[str, object]],
    ) -> None:
        """Test that HTTP 502 halves the batch and retries."""
        responses.post(GRAPHQL_URL, status=502)
        responses.post(GRAPHQL_URL, json={"data": batch_response(slug="nuxt/nuxt")})
        responses.post(GRAPHQL_URL, json={"data": batch_response(slug="unjs/nitro")})

        enriched = enrich_batch(
            ["nuxt/nuxt", "unjs/nitro"],
            "testuser",
            "MDQ6VXNlcjQ=",
        )

        assert sorted(enriched) == ["nuxt/nuxt", "unjs/nitro"]
        assert len(responses.calls) == 3

    @responses.activate
    def test_gives_up_on_a_single_repository(self) -> None:
        """Test that a lone repository GitHub cannot answer is skipped."""
        responses.post(GRAPHQL_URL, status=502)

        assert enrich_batch(["nuxt/nuxt"], "testuser", "MDQ6VXNlcjQ=") == {}
