"""Test configuration and fixtures."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(autouse=True)
def _set_test_env() -> Generator[None, None, None]:
    """Set required environment variables for tests."""
    old_prefix = os.environ.get("DEFAULT_PREFIX")
    os.environ["DEFAULT_PREFIX"] = "testuser"
    yield
    if old_prefix is None:
        os.environ.pop("DEFAULT_PREFIX", None)
    else:
        os.environ["DEFAULT_PREFIX"] = old_prefix


@pytest.fixture(autouse=True)
def reset_rate_limit_state() -> Generator[None, None, None]:
    """Reset rate limit state before each test."""
    from src.main import _rate_limit_state

    # Reset before test
    _rate_limit_state.skip_until = 0.0
    yield
    # Reset after test
    _rate_limit_state.skip_until = 0.0


@pytest.fixture
def sample_html() -> str:
    """Sample GitHub profile pins HTML."""
    return """
    <ul data-filter-list class="list-style-none position-relative">
        <li class="source" data-pinnable-type="repository">
            <input type="checkbox" checked>
            <label class="pinned-item-name">
                <svg></svg>
                <strong data-filter-item-text>nuxt/nuxt</strong>
                <span class="stars">59.3k<svg></svg></span>
            </label>
        </li>
        <li class="source" data-pinnable-type="repository">
            <input type="checkbox" checked>
            <label class="pinned-item-name">
                <svg></svg>
                <strong data-filter-item-text>testuser/test-repo</strong>
                <span class="stars">123<svg></svg></span>
            </label>
        </li>
        <li class="source" data-pinnable-type="gist">
            <input type="checkbox">
            <label class="pinned-item-name">
                <svg></svg>
                <strong data-filter-item-text>Some Gist</strong>
            </label>
        </li>
    </ul>
    """


@pytest.fixture
def sample_repo_response() -> dict[str, object]:
    """Sample GitHub API repository response."""
    return {
        "name": "test-repo",
        "full_name": "testuser/test-repo",
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
    }


@pytest.fixture
def nuxt_repo_response() -> dict[str, object]:
    """Sample GitHub API repository response for nuxt/nuxt."""
    return {
        "name": "nuxt",
        "full_name": "nuxt/nuxt",
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
    }


@pytest.fixture
def temp_cache_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary cache directory."""
    cache_dir = tmp_path / "test_cache"
    cache_dir.mkdir()
    yield cache_dir


@pytest.fixture
def temp_input_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary input directory."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    yield input_dir


@pytest.fixture
def temp_output_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary output directory."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    yield output_dir


@pytest.fixture
def mock_env(monkeypatch: pytest.MonkeyPatch, temp_cache_dir: Path) -> None:
    """Set up mock environment variables and directories."""
    monkeypatch.setenv("GITHUB_TOKEN", "test_token_12345")
    monkeypatch.setattr("src.main.CACHE_DIR", str(temp_cache_dir))
    monkeypatch.setattr(
        "src.main.Path", lambda x: temp_cache_dir if x == "github_cache" else Path(x)
    )
