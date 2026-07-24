# Copyright (c) 2026 Jonas Thelemann
"""Unit tests for main module."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest
import requests
import responses
from responses import matchers

from src.main import (
    RateLimitState,
    cache_path,
    enrich_repo,
    extract_repos_and_stars,
    github_api_get,
    parse_star_count,
)


@pytest.mark.unit
class TestParseStarCount:
    """Tests for parse_star_count function."""

    def test_parse_plain_number(self) -> None:
        """Test parsing plain numbers."""
        assert parse_star_count("123") == 123
        assert parse_star_count("1") == 1
        assert parse_star_count("999") == 999

    def test_parse_k_notation(self) -> None:
        """Test parsing K notation."""
        assert parse_star_count("1.5k") == 1500
        assert parse_star_count("59.3k") == 59300
        assert parse_star_count("1k") == 1000

    def test_parse_m_notation(self) -> None:
        """Test parsing M notation."""
        assert parse_star_count("1.5m") == 1500000
        assert parse_star_count("2m") == 2000000

    def test_parse_with_whitespace(self) -> None:
        """Test parsing with whitespace."""
        assert parse_star_count("  123  ") == 123
        assert parse_star_count(" 1.5k ") == 1500


@pytest.mark.unit
class TestCachePath:
    """Tests for cache_path function."""

    def test_generates_consistent_path(self) -> None:
        """Test that same endpoint generates same path."""
        path1 = cache_path("/repos/test/repo")
        path2 = cache_path("/repos/test/repo")
        assert path1 == path2

    def test_generates_different_paths(self) -> None:
        """Test that different endpoints generate different paths."""
        path1 = cache_path("/repos/test/repo1")
        path2 = cache_path("/repos/test/repo2")
        assert path1 != path2

    def test_path_is_in_cache_dir(self) -> None:
        """Test that generated path is in cache directory."""
        path = cache_path("/repos/test/repo")
        assert "github_cache" in str(path)
        assert path.suffix == ".json"


@pytest.mark.unit
class TestRateLimitState:
    """Tests for RateLimitState class."""

    def test_initial_not_skipping(self) -> None:
        """Test initial state is not skipping."""
        state = RateLimitState()
        assert not state.is_skipping()

    def test_set_skip_until_future(self) -> None:
        """Test setting skip until future time."""
        state = RateLimitState()
        future_time = time.time() + 100
        state.set_skip_until(future_time)
        assert state.is_skipping()

    def test_set_skip_until_past(self) -> None:
        """Test setting skip until past time."""
        state = RateLimitState()
        past_time = time.time() - 100
        state.set_skip_until(past_time)
        assert not state.is_skipping()


@pytest.mark.unit
class TestGitHubApiGet:
    """Tests for github_api_get function."""

    @responses.activate
    def test_successful_api_call(self, tmp_path: Path) -> None:
        """Test successful API call and caching."""
        # Mock the API response
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test/repo",
            json={"name": "repo", "stargazers_count": 100},
            status=200,
        )

        with patch("src.main.CACHE_DIR", str(tmp_path)):
            result = github_api_get("/repos/test/repo")

        assert result["name"] == "repo"
        assert result["stargazers_count"] == 100

        # Check that cache file was created
        cache_files = list(tmp_path.glob("*.json"))
        assert len(cache_files) == 1

    def test_cached_response(self, tmp_path: Path) -> None:
        """Test that cached response is used."""
        # Create a cache file
        cache_file = tmp_path / "test.json"
        cache_data = {"name": "cached_repo", "stargazers_count": 200}
        cache_file.write_text(json.dumps(cache_data))

        with patch("src.main.cache_path", return_value=cache_file):
            result = github_api_get("/repos/test/repo")

        assert result["name"] == "cached_repo"
        assert result["stargazers_count"] == 200

    @responses.activate
    def test_rate_limit_403(self, tmp_path: Path) -> None:
        """Test handling of 403 rate limit response."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test/repo",
            json={"message": "API rate limit exceeded"},
            status=403,
            headers={"x-ratelimit-reset": str(int(time.time() + 3600))},
        )

        with patch("src.main.CACHE_DIR", str(tmp_path)):
            result = github_api_get("/repos/test/repo")

        assert result == {}

    @responses.activate
    def test_rate_limit_429(self, tmp_path: Path) -> None:
        """Test handling of 429 rate limit response."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test/repo",
            json={"message": "API rate limit exceeded"},
            status=429,
            headers={"x-ratelimit-reset": str(int(time.time() + 3600))},
        )

        with patch("src.main.CACHE_DIR", str(tmp_path)):
            result = github_api_get("/repos/test/repo")

        assert result == {}

    @responses.activate
    def test_non_standard_error(self, tmp_path: Path) -> None:
        """Test handling of non-standard error status codes."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test/repo",
            json={"message": "Bad Request"},
            status=400,
        )

        with patch("src.main.CACHE_DIR", str(tmp_path)):
            result = github_api_get("/repos/test/repo")

        assert result == {}

    @responses.activate
    def test_server_error(self, tmp_path: Path) -> None:
        """Test handling of server errors."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test/repo",
            json={"message": "Internal Server Error"},
            status=500,
        )

        with patch("src.main.CACHE_DIR", str(tmp_path)):
            result = github_api_get("/repos/test/repo")

        assert result == {}

    @responses.activate
    def test_other_error(self, tmp_path: Path) -> None:
        """Test handling of other HTTP errors."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test/repo",
            json={"message": "Not Found"},
            status=404,
        )

        with patch("src.main.CACHE_DIR", str(tmp_path)):
            result = github_api_get("/repos/test/repo")

        assert result == {}

    @responses.activate
    def test_with_github_token(self, tmp_path: Path) -> None:
        """Test that GitHub token is included in headers."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test/repo",
            json={"name": "repo"},
            status=200,
            match=[matchers.header_matcher({"Authorization": "token test_token_123"})],
        )

        with (
            patch("src.main.CACHE_DIR", str(tmp_path)),
            patch("src.main.GITHUB_TOKEN", "test_token_123"),
        ):
            result = github_api_get("/repos/test/repo")

        assert result["name"] == "repo"

    @responses.activate
    def test_skip_during_rate_limit(self, tmp_path: Path) -> None:
        """Test that requests are skipped when rate limited."""
        from src.main import _rate_limit_state

        _rate_limit_state.set_skip_until(time.time() + 100)

        with patch("src.main.CACHE_DIR", str(tmp_path)):
            result = github_api_get("/repos/test/repo")

        # Should return empty dict without making request
        assert result == {}
        assert len(responses.calls) == 0

    @responses.activate
    def test_request_exception(self, tmp_path: Path) -> None:
        """Test handling of request exceptions."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test/repo",
            body=requests.exceptions.ConnectionError("Connection failed"),
        )

        with patch("src.main.CACHE_DIR", str(tmp_path)):
            result = github_api_get("/repos/test/repo")

        assert result == {}


@pytest.mark.unit
class TestEnrichRepo:
    """Tests for enrich_repo function."""

    @responses.activate
    def test_enrich_with_owner(
        self, tmp_path: Path, sample_repo_response: dict[str, object]
    ) -> None:
        """Test enriching a repo with owner prefix."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/testuser/test-repo",
            json=sample_repo_response,
            status=200,
        )

        with patch("src.main.CACHE_DIR", str(tmp_path)):
            result = enrich_repo("testuser/test-repo")

        assert result["repository"]["name"] == "test-repo"
        assert result["repository"]["stars"] == 123
        assert result["repository"]["owner"]["name"] == "testuser"

    @responses.activate
    def test_enrich_without_owner(
        self, tmp_path: Path, sample_repo_response: dict[str, object]
    ) -> None:
        """Test enriching a repo without owner prefix."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/testuser/test-repo",
            json=sample_repo_response,
            status=200,
        )

        with patch("src.main.CACHE_DIR", str(tmp_path)):
            result = enrich_repo("test-repo")

        assert result["repository"]["name"] == "test-repo"

    @responses.activate
    def test_enrich_with_empty_response(self, tmp_path: Path) -> None:
        """Test enriching with empty API response."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/testuser/test-repo",
            json={},
            status=200,
        )

        with patch("src.main.CACHE_DIR", str(tmp_path)):
            result = enrich_repo("test-repo")

        # Should not raise errors, values should be None
        assert result["repository"]["name"] is None
        assert result["repository"]["stars"] is None

    @responses.activate
    def test_enrich_with_invalid_owner(self, tmp_path: Path) -> None:
        """Test enriching when owner is not a dict."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/testuser/test-repo",
            json={"name": "test-repo", "owner": "not-a-dict"},  # Invalid owner type
            status=200,
        )

        with patch("src.main.CACHE_DIR", str(tmp_path)):
            result = enrich_repo("test-repo")

        # Should handle gracefully with None values for owner
        assert result["repository"]["name"] == "test-repo"
        assert result["repository"]["owner"]["name"] is None


@pytest.mark.unit
class TestExtractReposAndStars:
    """Tests for extract_repos_and_stars function."""

    @responses.activate
    def test_extract_from_html(
        self,
        tmp_path: Path,
        sample_html: str,
        nuxt_repo_response: dict[str, object],
        sample_repo_response: dict[str, object],
    ) -> None:
        """Test extracting repos from HTML."""
        responses.add(
            responses.GET,
            "https://api.github.com/repos/nuxt/nuxt",
            json=nuxt_repo_response,
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.github.com/repos/testuser/test-repo",
            json=sample_repo_response,
            status=200,
        )

        with patch("src.main.CACHE_DIR", str(tmp_path)):
            results = extract_repos_and_stars(sample_html)

        assert len(results) == 2
        assert results[0]["repository"]["name"] == "nuxt"
        assert results[0]["repository"]["stars"] == 59300
        assert results[1]["repository"]["name"] == "test-repo"

    @responses.activate
    def test_extract_with_fallback_stars(
        self, tmp_path: Path, sample_html: str
    ) -> None:
        """Test that HTML star count is used as fallback."""
        # Mock API to return no star count
        responses.add(
            responses.GET,
            "https://api.github.com/repos/nuxt/nuxt",
            json={"name": "nuxt"},  # Missing stargazers_count
            status=200,
        )
        responses.add(
            responses.GET,
            "https://api.github.com/repos/testuser/test-repo",
            json={"name": "test-repo"},  # Missing stargazers_count
            status=200,
        )

        with patch("src.main.CACHE_DIR", str(tmp_path)):
            results = extract_repos_and_stars(sample_html)

        # Should use HTML star counts as fallback
        assert results[0]["repository"]["stars"] == 59300
        assert results[1]["repository"]["stars"] == 123

    def test_extract_empty_html(self, tmp_path: Path) -> None:
        """Test extracting from empty HTML."""
        with patch("src.main.CACHE_DIR", str(tmp_path)):
            results = extract_repos_and_stars("<html></html>")

        assert results == []

    def test_extract_html_without_name_element(self, tmp_path: Path) -> None:
        """Test extracting from HTML with missing name element."""
        html = """
        <ul>
            <li class="source" data-pinnable-type="repository">
                <span class="stars">100</span>
            </li>
        </ul>
        """
        with patch("src.main.CACHE_DIR", str(tmp_path)):
            results = extract_repos_and_stars(html)

        # Should skip entries without name element
        assert results == []
