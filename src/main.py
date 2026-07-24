# Copyright (c) 2026 Jonas Thelemann
"""Extract and enrich GitHub pinned repository data from profile pins HTML."""

from __future__ import annotations

import json
import logging
import os
import time
from hashlib import sha256
from pathlib import Path
from typing import TypedDict

import requests
from bs4 import BeautifulSoup

# ---------------- CONFIG ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

CACHE_DIR = "github_cache"
REQUEST_TIMEOUT = 30  # seconds
HTTP_OK = 200
HTTP_SERVER_ERROR = 500
# Required: GitHub username prefix for repositories without owner
DEFAULT_PREFIX = os.environ["DEFAULT_PREFIX"]
# Optional: GitHub token for higher API rate limits
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
# ----------------------------------------

Path(CACHE_DIR).mkdir(exist_ok=True)


class OwnerDict(TypedDict, total=False):
    """Owner information dictionary."""

    avatar_url: str | None
    name: str | None
    type: str | None
    url: str | None


class RepositoryDict(TypedDict, total=False):
    """Repository information dictionary."""

    description: str | None
    fork: bool | None
    name: str | None
    owner: OwnerDict
    stars: int | None
    url: str | None


class EnrichedRepoDict(TypedDict):
    """Complete enriched repository data."""

    repository: RepositoryDict


class RateLimitState:
    """Rate limit state tracker."""

    def __init__(self) -> None:
        """Initialize rate limit state."""
        self.skip_until = float(0)

    def is_skipping(self) -> bool:
        """Check if we should skip due to rate limiting."""
        return time.time() < self.skip_until

    def set_skip_until(self, timestamp: float) -> None:
        """Set the timestamp until which to skip requests."""
        self.skip_until = timestamp


_rate_limit_state = RateLimitState()


def cache_path(endpoint: str) -> Path:
    """Generate a safe filename for the endpoint."""
    h = sha256(endpoint.encode("utf-8")).hexdigest()
    return Path(CACHE_DIR) / f"{h}.json"


def parse_star_count(text: str) -> int:
    """Parse star count from text with K/M notation."""
    text = text.strip().lower()
    if text.endswith("k"):
        return int(float(text[:-1]) * 1_000)
    if text.endswith("m"):
        return int(float(text[:-1]) * 1_000_000)
    return int(text)


def github_api_get(endpoint: str) -> dict[str, object]:
    """Fetch data from GitHub API with caching and rate limit handling."""
    # If currently skipping due to previous rate-limit
    if _rate_limit_state.is_skipping():
        skip_time = time.ctime(_rate_limit_state.skip_until)
        logger.info(
            "Skipping GitHub fetch for %s due to recent rate-limit (skip until %s)",
            endpoint,
            skip_time,
        )
        return {}

    # Return cached data if available
    path = cache_path(endpoint)
    if path.exists():
        data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
        return data

    url = f"https://api.github.com{endpoint}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)

        if resp.status_code == HTTP_OK:
            data_response: dict[str, object] = resp.json()
            # Save to cache
            path.write_text(
                json.dumps(data_response, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return data_response

        if resp.status_code in {403, 429}:
            # Rate limit hit, set skip-until timestamp
            retry_after = resp.headers.get("x-ratelimit-reset")
            skip_until = float(retry_after) if retry_after else time.time() + 60
            _rate_limit_state.set_skip_until(skip_until)
            logger.warning(
                "GitHub rate limit reached. Skipping all fetches until %s",
                time.ctime(skip_until),
            )
            return {}

        if resp.status_code >= HTTP_SERVER_ERROR:
            logger.warning(
                "GitHub server error %d on %s. Returning empty dict",
                resp.status_code,
                endpoint,
            )
            return {}

        logger.error("GitHub API %s returned %d", endpoint, resp.status_code)

    except requests.RequestException:
        logger.exception("Request failed for %s", endpoint)

    return {}


def enrich_repo(repo_name: str) -> EnrichedRepoDict:
    """Given a repository string "owner/repo" or "repo", return enriched data.

    Returns owner name, profile image, and repo description.
    """
    if "/" not in repo_name:
        repo_name = f"{DEFAULT_PREFIX}/{repo_name}"

    owner, repo = repo_name.split("/", 1)

    repo_data = github_api_get(f"/repos/{owner}/{repo}")
    owner_data = repo_data.get("owner") or {}

    if not isinstance(owner_data, dict):
        owner_data = {}

    def safe_str(value: object) -> str | None:
        """Safely cast to str or return None."""
        return value if isinstance(value, str) else None

    def safe_bool(value: object) -> bool | None:
        """Safely cast to bool or return None."""
        return value if isinstance(value, bool) else None

    def safe_int(value: object) -> int | None:
        """Safely cast to int or return None."""
        return value if isinstance(value, int) else None

    return EnrichedRepoDict(
        repository=RepositoryDict(
            description=safe_str(repo_data.get("description")),
            fork=safe_bool(repo_data.get("fork")),
            name=safe_str(repo_data.get("name")),
            owner=OwnerDict(
                avatar_url=safe_str(owner_data.get("avatar_url")),
                name=safe_str(owner_data.get("login")),
                type=safe_str(owner_data.get("type")),
                url=safe_str(owner_data.get("html_url")),
            ),
            stars=safe_int(repo_data.get("stargazers_count")),
            url=safe_str(repo_data.get("html_url")),
        )
    )


def extract_repos_and_stars(html: str) -> list[EnrichedRepoDict]:
    """Extract repository data from profile pins HTML."""
    soup = BeautifulSoup(html, "lxml")
    results: list[EnrichedRepoDict] = []

    for li in soup.select("li.source[data-pinnable-type='repository']"):
        # Repo name
        name_el = li.select_one("strong[data-filter-item-text]")
        if not name_el:
            continue
        repo_name = name_el.get_text(strip=True)

        # Star count from HTML (optional, API will override)
        stars_el = li.select_one("span.stars")
        star_count = None
        if stars_el:
            star_text = stars_el.find(string=True, recursive=False)
            if star_text:
                star_count = parse_star_count(star_text)

        # Enrich with GitHub API
        enriched = enrich_repo(repo_name)
        # fallback: use HTML stars if API failed
        if enriched["repository"]["stars"] is None and star_count is not None:
            enriched["repository"]["stars"] = star_count

        results.append(enriched)

    return results


def main() -> None:
    """Run the profile pins extraction and enrichment."""
    input_path = Path("input/profile-pins.html")
    html = input_path.read_text(encoding="utf-8")

    data = extract_repos_and_stars(html)

    # Output as JSON
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

    output_path = output_dir / "repos.json"
    output_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    logger.info("Output written to %s", output_path)


if __name__ == "__main__":
    main()
