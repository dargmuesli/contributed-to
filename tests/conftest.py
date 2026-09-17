# Copyright (c) 2026 Jonas Thelemann
"""Test configuration and fixtures."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path

GRAPHQL_URL = "https://api.github.com/graphql"


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
def _github_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Give the module a token and a cache directory of its own.

    Both are module constants read at import time.
    So they have to be patched on the module rather than in the environment.
    """
    monkeypatch.setattr("src.main.GITHUB_TOKEN", "test_token_12345")
    monkeypatch.setattr("src.main.CACHE_DIR", str(tmp_path / "github_cache"))


@pytest.fixture
def sample_html() -> str:
    """Sample GitHub profile pins HTML."""
    return """
    <ul data-filter-list class="list-style-none position-relative">
        <li class="source" data-pinnable-type="repository">
            <input type="checkbox" checked>
            <label class="pinned-item-name">
                <strong data-filter-item-text>nuxt/nuxt</strong>
            </label>
        </li>
        <li class="source" data-pinnable-type="repository">
            <input type="checkbox" checked>
            <label class="pinned-item-name">
                <strong data-filter-item-text>test-repo</strong>
            </label>
        </li>
        <li class="source" data-pinnable-type="gist">
            <input type="checkbox">
            <label class="pinned-item-name">
                <strong data-filter-item-text>Some Gist</strong>
            </label>
        </li>
    </ul>
    """


@pytest.fixture
def repo_node() -> Callable[..., dict[str, object]]:
    """Return a factory for one repository as the GraphQL API reports it."""

    def build(slug: str = "nuxt/nuxt", **overrides: object) -> dict[str, object]:
        owner, name = slug.split("/", 1)
        node: dict[str, object] = {
            "defaultBranchRef": {
                "target": {
                    "history": {"totalCount": 6},
                    "latest": {"nodes": [{"committedDate": "2026-01-29T09:39:30Z"}]},
                },
            },
            "description": "The full-stack Vue framework.",
            "isFork": False,
            "name": name,
            "nameWithOwner": slug,
            "owner": {
                "__typename": "Organization",
                "avatarUrl": f"https://avatars.githubusercontent.com/{owner}",
                "login": owner,
                "url": f"https://github.com/{owner}",
            },
            "stargazerCount": 60872,
            "url": f"https://github.com/{slug}",
            "viewerPermission": "TRIAGE",
        }
        node.update(overrides)
        return node

    return build


@pytest.fixture
def batch_response(
    repo_node: Callable[..., dict[str, object]],
) -> Callable[..., dict[str, object]]:
    """Return a factory for a complete batch response covering one repository."""

    def build(
        index: int = 0,
        slug: str = "nuxt/nuxt",
        **overrides: object,
    ) -> dict[str, object]:
        return {
            f"issues{index}": {"issueCount": 15},
            f"merged{index}": {
                "issueCount": 6,
                "nodes": [{"mergedAt": "2026-01-29T09:39:31Z"}],
            },
            f"repo{index}": repo_node(slug, **overrides),
            f"reviews{index}": {"issueCount": 5},
        }

    return build
