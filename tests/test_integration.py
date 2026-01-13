"""Integration tests for the complete workflow."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import responses

from src.main import main


@pytest.mark.integration
class TestMainIntegration:
    """Integration tests for main function."""

    @responses.activate
    def test_full_workflow(self, tmp_path: Path, sample_html: str) -> None:
        """Test the complete workflow from HTML to JSON output."""
        # Set up directories
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        cache_dir = tmp_path / "cache"

        input_dir.mkdir()
        cache_dir.mkdir()

        # Create input file
        input_file = input_dir / "profile-pins.html"
        input_file.write_text(sample_html)

        # Mock API responses
        responses.add(
            responses.GET,
            "https://api.github.com/repos/nuxt/nuxt",
            json={
                "name": "nuxt",
                "description": "The Intuitive Vue Framework.",
                "fork": False,
                "stargazers_count": 59300,
                "html_url": "https://github.com/nuxt/nuxt",
                "owner": {
                    "login": "nuxt",
                    "avatar_url": "https://avatars.githubusercontent.com/u/23360933",
                    "type": "Organization",
                    "html_url": "https://github.com/nuxt",
                },
            },
            status=200,
        )

        responses.add(
            responses.GET,
            "https://api.github.com/repos/testuser/test-repo",
            json={
                "name": "test-repo",
                "description": "A test repository",
                "fork": False,
                "stargazers_count": 123,
                "html_url": "https://github.com/testuser/test-repo",
                "owner": {
                    "login": "testuser",
                    "avatar_url": "https://avatars.githubusercontent.com/u/12345",
                    "type": "User",
                    "html_url": "https://github.com/testuser",
                },
            },
            status=200,
        )

        # Patch paths and run
        with (
            patch("src.main.CACHE_DIR", str(cache_dir)),
            patch("src.main.Path") as mock_path_class,
        ):
            # Configure Path mock
            def path_factory(path_str: str) -> Path:
                if path_str == "github_cache":
                    return cache_dir
                if path_str == "input/profile-pins.html":
                    return input_file
                if path_str == "output":
                    return output_dir
                return Path(path_str)

            mock_path_class.side_effect = path_factory

            main()

        # Verify output file was created
        output_file = output_dir / "repos.json"
        assert output_file.exists()

        # Verify output content
        output_data = json.loads(output_file.read_text())
        assert len(output_data) == 2

        # Check first repo (nuxt/nuxt)
        assert output_data[0]["repository"]["name"] == "nuxt"
        assert output_data[0]["repository"]["stars"] == 59300
        assert output_data[0]["repository"]["owner"]["name"] == "nuxt"

        # Check second repo
        assert output_data[1]["repository"]["name"] == "test-repo"
        assert output_data[1]["repository"]["stars"] == 123

    @responses.activate
    def test_workflow_with_caching(self, tmp_path: Path, sample_html: str) -> None:
        """Test that caching works across multiple runs."""
        # Set up directories
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        cache_dir = tmp_path / "cache"

        input_dir.mkdir()
        cache_dir.mkdir()

        input_file = input_dir / "profile-pins.html"
        input_file.write_text(sample_html)

        # Mock API responses for first run only
        responses.add(
            responses.GET,
            "https://api.github.com/repos/nuxt/nuxt",
            json={"name": "nuxt", "stargazers_count": 59300, "owner": {}},
            status=200,
        )

        responses.add(
            responses.GET,
            "https://api.github.com/repos/testuser/test-repo",
            json={"name": "test-repo", "stargazers_count": 123, "owner": {}},
            status=200,
        )

        def path_factory(path_str: str) -> Path:
            if path_str == "github_cache":
                return cache_dir
            if path_str == "input/profile-pins.html":
                return input_file
            if path_str == "output":
                return output_dir
            return Path(path_str)

        # First run - should hit API
        with (
            patch("src.main.CACHE_DIR", str(cache_dir)),
            patch("src.main.Path") as mock_path_class,
        ):
            mock_path_class.side_effect = path_factory
            main()

        # Verify cache files were created
        cache_files = list(cache_dir.glob("*.json"))
        assert len(cache_files) == 2

        # Clear API mocks
        responses.reset()

        # Second run - should use cache, no API calls
        with (
            patch("src.main.CACHE_DIR", str(cache_dir)),
            patch("src.main.Path") as mock_path_class,
        ):
            mock_path_class.side_effect = path_factory
            main()

        # Verify output still correct (from cache)
        output_file = output_dir / "repos.json"
        output_data = json.loads(output_file.read_text())
        assert len(output_data) == 2

    @responses.activate
    def test_workflow_with_rate_limiting(self, tmp_path: Path) -> None:
        """Test that rate limiting is handled properly."""
        html = """
        <ul>
            <li class="source" data-pinnable-type="repository">
                <strong data-filter-item-text>test/repo1</strong>
                <span class="stars">100</span>
            </li>
            <li class="source" data-pinnable-type="repository">
                <strong data-filter-item-text>test/repo2</strong>
                <span class="stars">200</span>
            </li>
        </ul>
        """

        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        cache_dir = tmp_path / "cache"

        input_dir.mkdir()
        cache_dir.mkdir()

        input_file = input_dir / "profile-pins.html"
        input_file.write_text(html)

        # First API call succeeds
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test/repo1",
            json={"name": "repo1", "stargazers_count": 100, "owner": {}},
            status=200,
        )

        # Second API call hits rate limit
        responses.add(
            responses.GET,
            "https://api.github.com/repos/test/repo2",
            json={"message": "API rate limit exceeded"},
            status=429,
            headers={"x-ratelimit-reset": str(int(time.time() + 3600))},
        )

        def path_factory(path_str: str) -> Path:
            if path_str == "github_cache":
                return cache_dir
            if path_str == "input/profile-pins.html":
                return input_file
            if path_str == "output":
                return output_dir
            return Path(path_str)

        with (
            patch("src.main.CACHE_DIR", str(cache_dir)),
            patch("src.main.Path") as mock_path_class,
        ):
            mock_path_class.side_effect = path_factory
            main()

        # Output should still be created with available data
        output_file = output_dir / "repos.json"
        assert output_file.exists()

        output_data = json.loads(output_file.read_text())
        assert len(output_data) == 2

        # First repo should have API data
        assert output_data[0]["repository"]["name"] == "repo1"

        # Second repo should fall back to HTML star count
        assert output_data[1]["repository"]["stars"] == 200
