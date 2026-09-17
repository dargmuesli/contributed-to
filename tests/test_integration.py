# Copyright (c) 2026 Jonas Thelemann
"""Integration tests covering the flow from discovery to written output."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
import responses

from src.main import GRAPHQL_BATCH_SIZE, cache_read, cache_write, enrich_repos, main
from tests.conftest import GRAPHQL_URL

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.mark.integration
class TestEnrichRepos:
    """Tests for enrich_repos function."""

    @responses.activate
    def test_rejects_slugs_that_are_not_repositories(self) -> None:
        """Test that an unusable slug is skipped without a request.

        The query builder interpolates slugs.
        So anything holding GraphQL syntax is dropped before it reaches a query.
        """
        assert enrich_repos({"not a slug", 'owner/repo") {'}, "testuser", "id") == []
        assert not responses.calls

    @responses.activate
    def test_serves_a_fresh_cache_entry_without_a_request(self) -> None:
        """Test that a cached repository is not fetched again."""
        cached = {
            "contribution": {"commits": 1},
            "repository": {"name": "nuxt", "stars": 1},
        }
        cache_write("contribution:nuxt/nuxt", cached)

        assert enrich_repos({"nuxt/nuxt"}, "testuser", "id") == [cached]
        assert not responses.calls

    @responses.activate
    def test_refetches_an_entry_of_an_older_shape(
        self,
        batch_response: Callable[..., dict[str, object]],
    ) -> None:
        """Test that a cache entry missing the current keys is fetched again."""
        cache_write("contribution:nuxt/nuxt", {"repository": {"name": "nuxt"}})
        responses.post(GRAPHQL_URL, json={"data": batch_response()})

        enriched = enrich_repos({"nuxt/nuxt"}, "testuser", "id")

        assert enriched[0]["contribution"]["commits"] == 6
        assert len(responses.calls) == 1

    @responses.activate
    def test_caches_what_it_fetches(
        self,
        batch_response: Callable[..., dict[str, object]],
    ) -> None:
        """Test that a fetched repository is written to the cache."""
        responses.post(GRAPHQL_URL, json={"data": batch_response()})

        enrich_repos({"nuxt/nuxt"}, "testuser", "id")

        assert cache_read("contribution:nuxt/nuxt") is not None

    @responses.activate
    def test_splits_more_repositories_than_fit_in_one_query(
        self,
        batch_response: Callable[..., dict[str, object]],
    ) -> None:
        """Test that the batch size caps how many repositories go into one request."""
        slugs = {f"owner{index}/repo" for index in range(GRAPHQL_BATCH_SIZE + 1)}
        for _ in range(2):
            responses.post(
                GRAPHQL_URL,
                json={
                    "data": {
                        key: value
                        for index in range(GRAPHQL_BATCH_SIZE)
                        for key, value in batch_response(
                            index=index,
                            slug=f"owner{index}/repo",
                        ).items()
                    },
                },
            )

        enrich_repos(slugs, "testuser", "id")

        assert len(responses.calls) == 2

    @responses.activate
    def test_sorts_the_output(
        self,
        batch_response: Callable[..., dict[str, object]],
    ) -> None:
        """Test that the output order is stable regardless of discovery order."""
        responses.post(
            GRAPHQL_URL,
            json={
                "data": {
                    **batch_response(index=0, slug="unjs/nitro"),
                    **batch_response(index=1, slug="nuxt/nuxt"),
                },
            },
        )

        enriched = enrich_repos({"unjs/nitro", "nuxt/nuxt"}, "testuser", "id")

        assert [item["repository"]["name"] for item in enriched] == ["nuxt", "nitro"]


@pytest.mark.integration
class TestMain:
    """Tests for the main entry point."""

    @responses.activate
    def test_writes_the_output_file(
        self,
        batch_response: Callable[..., dict[str, object]],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Test that a full run discovers, enriches and writes."""
        monkeypatch.chdir(tmp_path)
        responses.post(GRAPHQL_URL, json={"data": {"user": {"id": "MDQ6VXNlcjQ="}}})
        responses.post(
            GRAPHQL_URL,
            json={
                "data": {
                    "user": {
                        "repositoriesContributedTo": {
                            "nodes": [{"isFork": False, "nameWithOwner": "nuxt/nuxt"}],
                            "pageInfo": {"endCursor": None, "hasNextPage": False},
                        },
                    },
                },
            },
        )
        responses.post(GRAPHQL_URL, json={"data": batch_response()})

        main()

        output = (tmp_path / "output/repos.json").read_text()
        data = json.loads(output)

        assert output.endswith("\n")
        assert len(data) == 1
        assert data[0]["repository"]["name"] == "nuxt"
        assert data[0]["contribution"]["role"] == "TRIAGE"
