# Copyright (c) 2026 Jonas Thelemann
"""Discover the GitHub projects a user contributed to and enrich them."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from hashlib import sha256
from pathlib import Path
from typing import TypedDict, cast

import requests
from bs4 import BeautifulSoup

# ---------------- CONFIG ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

CACHE_DIR = "github_cache"
# Contribution counts change whenever the user pushes, so cached entries expire daily.
# The previous cache never expired, which froze stars and descriptions.
CACHE_TTL_SECONDS = 24 * 60 * 60
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
# A batch of 25 repositories makes the GraphQL endpoint time out with HTTP 502.
# Eight sits comfortably below that.
GRAPHQL_BATCH_SIZE = 8
HTTP_BAD_GATEWAY = 502
HTTP_OK = 200
PINS_INPUT_PATH = "input/profile-pins.html"
SEED_INPUT_PATH = "input/seed.json"
REQUEST_TIMEOUT = 60  # seconds
# GitHub only permits these characters in owner and repository names.
# Validating up front lets the query builder interpolate names without escaping them.
REPO_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
# Required: the GitHub user whose contributions are collected.
DEFAULT_PREFIX = os.environ["DEFAULT_PREFIX"]
# Required: the GraphQL API rejects unauthenticated requests.
# `viewerPermission` reads the token owner's own permission.
# So this has to be a token of DEFAULT_PREFIX.
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
# ----------------------------------------


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


class ContributionDict(TypedDict, total=False):
    """What the user did in a repository, as opposed to what the repository is."""

    commits: int
    issues: int
    last_activity_at: str | None
    pull_requests_merged: int
    reviews: int
    role: str | None


class EnrichedRepoDict(TypedDict):
    """Complete enriched repository data."""

    contribution: ContributionDict
    repository: RepositoryDict


class MissingTokenError(RuntimeError):
    """Raised when no GitHub token is configured."""

    def __init__(self) -> None:
        """Explain that the GraphQL API cannot be queried anonymously."""
        super().__init__(
            "GITHUB_TOKEN is required: the GraphQL API rejects anonymous requests "
            "and roles are read from the token owner's own permissions",
        )


class GitHubGraphQlError(RuntimeError):
    """Raised when the GraphQL API reports an error."""

    def __init__(self, detail: str) -> None:
        """Carry the API's own error text."""
        super().__init__(f"GitHub GraphQL API error: {detail}")


class BatchTooLargeError(RuntimeError):
    """Raised when GitHub cannot answer a batch within its own time budget."""

    def __init__(self) -> None:
        """Signal to the caller that the batch has to be split."""
        super().__init__("GitHub GraphQL API returned HTTP 502, the batch is too large")


class UnknownUserError(RuntimeError):
    """Raised when the configured user does not exist."""

    def __init__(self, login: str) -> None:
        """Name the login that could not be resolved."""
        super().__init__(f"GitHub user {login} does not exist")


def _as_dict(value: object) -> dict[str, object]:
    """Return a mapping unchanged, or an empty one for anything else."""
    return value if isinstance(value, dict) else {}


def cache_path(key: str) -> Path:
    """Generate a safe filename for a cache key."""
    h = sha256(key.encode("utf-8")).hexdigest()
    return Path(CACHE_DIR) / f"{h}.json"


def cache_read(key: str) -> dict[str, object] | None:
    """Return the cached value for a key, or None when it is absent or stale."""
    path = cache_path(key)

    if not path.exists():
        return None

    if time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
        logger.info("Cache entry for %s expired", key)
        return None

    data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return data


def cache_write(key: str, value: dict[str, object]) -> None:
    """Store a value under a cache key."""
    Path(CACHE_DIR).mkdir(exist_ok=True)
    cache_path(key).write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def github_graphql(query: str, variables: dict[str, object]) -> dict[str, object]:
    """Run a GraphQL query and return its data.

    BatchTooLargeError means GitHub gave up; retry with fewer aliases.
    GitHubGraphQlError covers everything else.
    """
    if not GITHUB_TOKEN:
        raise MissingTokenError

    resp = requests.post(
        GITHUB_GRAPHQL_URL,
        json={"query": query, "variables": variables},
        headers={"Authorization": f"bearer {GITHUB_TOKEN}"},
        timeout=REQUEST_TIMEOUT,
    )

    if resp.status_code == HTTP_BAD_GATEWAY:
        raise BatchTooLargeError

    if resp.status_code != HTTP_OK:
        detail = f"HTTP {resp.status_code}: {resp.text[:200]}"
        raise GitHubGraphQlError(detail)

    payload = resp.json()

    # A partial response carries errors and data when one repository is inaccessible.
    # Those nulls are handled per repository, so only fail when nothing came back.
    if payload.get("errors") and not payload.get("data"):
        raise GitHubGraphQlError(json.dumps(payload["errors"])[:500])

    for error in payload.get("errors") or []:
        logger.warning("GraphQL error: %s", error.get("message", error))

    data: dict[str, object] = payload.get("data") or {}
    return data


def fetch_user_id(login: str) -> str:
    """Return a user's GraphQL node ID, needed to filter commit history by author."""
    query = "query($login: String!) { user(login: $login) { id } }"
    user = github_graphql(query, {"login": login}).get("user")

    if not isinstance(user, dict) or not isinstance(user.get("id"), str):
        raise UnknownUserError(login)

    return str(user["id"])


def discover_contributed_repos(login: str) -> set[str]:
    """Return the repositories GitHub itself reports as contributed to.

    GitHub has no API for pinned repositories, but it has one for contributions.
    That is what this output is actually about.
    Forks are left out: pushing a branch to your own fork is not a contribution.
    The pull request it opens upstream is.
    """
    query = """
query($login: String!, $cursor: String) {
  user(login: $login) {
    repositoriesContributedTo(
      first: 100
      after: $cursor
      includeUserRepositories: true
      contributionTypes: [COMMIT, ISSUE, PULL_REQUEST, PULL_REQUEST_REVIEW, REPOSITORY]
    ) {
      pageInfo { hasNextPage endCursor }
      nodes { isFork nameWithOwner }
    }
  }
}
"""
    slugs: set[str] = set()
    cursor: str | None = None

    while True:
        user = github_graphql(query, {"login": login, "cursor": cursor}).get("user")

        if not isinstance(user, dict):
            raise UnknownUserError(login)

        page = user.get("repositoriesContributedTo")

        if not isinstance(page, dict):
            break

        for node in page.get("nodes") or []:
            if isinstance(node, dict) and not node.get("isFork"):
                slugs.add(str(node["nameWithOwner"]))

        page_info = page.get("pageInfo") or {}

        if not page_info.get("hasNextPage"):
            break

        cursor = str(page_info["endCursor"])

    logger.info("Discovered %d repositories via the contributions API", len(slugs))
    return slugs


def extract_pinned_repos(html: str, default_prefix: str) -> set[str]:
    """Extract repository slugs from profile pins HTML.

    The pins picker reaches further back in time than the contributions API does.
    So it stays useful as a historical seed, even though it is exported by hand.
    """
    soup = BeautifulSoup(html, "lxml")
    slugs: set[str] = set()

    for li in soup.select("li.source[data-pinnable-type='repository']"):
        name_el = li.select_one("strong[data-filter-item-text]")

        if not name_el:
            continue

        slug = name_el.get_text(strip=True)

        if "/" not in slug:
            slug = f"{default_prefix}/{slug}"

        slugs.add(slug)

    logger.info("Read %d repositories from the pins seed", len(slugs))
    return slugs


def read_pins_seed(default_prefix: str) -> set[str]:
    """Read the optional pins HTML seed, returning nothing when it is absent."""
    path = Path(PINS_INPUT_PATH)

    if not path.exists():
        logger.info(
            "No pins seed at %s, relying on the contributions API", PINS_INPUT_PATH
        )
        return set()

    return extract_pinned_repos(path.read_text(encoding="utf-8"), default_prefix)


def read_json_seed() -> set[str]:
    """Read the slugs of a previous run's output, so the list never shrinks silently.

    The contributions API only reaches back so far, and the pins export is made by hand.
    Feeding the last output back in keeps both one-off imports permanent.
    """
    path = Path(SEED_INPUT_PATH)

    if not path.exists():
        logger.info("No JSON seed at %s", SEED_INPUT_PATH)
        return set()

    slugs = set()

    for entry in json.loads(path.read_text(encoding="utf-8")):
        repository = _as_dict(_as_dict(entry).get("repository"))
        owner = _as_dict(repository.get("owner")).get("name")
        name = repository.get("name")

        if isinstance(owner, str) and isinstance(name, str):
            slugs.add(f"{owner}/{name}")

    logger.info("Read %d repositories from the JSON seed", len(slugs))
    return slugs


def build_batch_query(slugs: list[str], login: str) -> str:
    """Build one aliased GraphQL query covering several repositories.

    Slugs are interpolated because an alias cannot share a variable with its siblings.
    REPO_SLUG_PATTERN guarantees they hold no GraphQL syntax.
    """
    parts = ["query($userId: ID!) {"]

    for index, slug in enumerate(slugs):
        owner, name = slug.split("/", 1)
        parts.append(f"""
  repo{index}: repository(owner: "{owner}", name: "{name}") {{
    description
    isFork
    name
    nameWithOwner
    stargazerCount
    url
    viewerPermission
    owner {{ avatarUrl login url __typename }}
    defaultBranchRef {{ target {{ ... on Commit {{
      history(author: {{id: $userId}}) {{ totalCount }}
      latest: history(author: {{id: $userId}}, first: 1) {{ nodes {{ committedDate }} }}
    }} }} }}
  }}
  merged{index}: search(
    query: "repo:{slug} author:{login} type:pr is:merged sort:updated-desc"
    type: ISSUE
    first: 1
  ) {{ issueCount nodes {{ ... on PullRequest {{ mergedAt }} }} }}
  reviews{index}: search(
    query: "repo:{slug} reviewed-by:{login} type:pr"
    type: ISSUE
    first: 0
  ) {{ issueCount }}
  issues{index}: search(
    query: "repo:{slug} author:{login} type:issue"
    type: ISSUE
    first: 0
  ) {{ issueCount }}""")

    parts.append("\n  rateLimit { cost remaining }\n}")
    return "".join(parts)


def _issue_count(value: object) -> int:
    """Read an issueCount from a search result."""
    count = _as_dict(value).get("issueCount")
    return count if isinstance(count, int) else 0


def _first_date(value: object, key: str) -> str | None:
    """Read a date from the first node of a connection."""
    nodes = _as_dict(value).get("nodes")

    if not isinstance(nodes, list) or not nodes:
        return None

    date = _as_dict(nodes[0]).get(key)
    return date if isinstance(date, str) else None


def _build_repository(repo: dict[str, object]) -> RepositoryDict:
    """Map a GraphQL repository onto the output shape."""
    owner = _as_dict(repo.get("owner"))

    def text(source: dict[str, object], key: str) -> str | None:
        """Read a string field, or None when it is absent or of another type."""
        value = source.get(key)
        return value if isinstance(value, str) else None

    stars = repo.get("stargazerCount")
    fork = repo.get("isFork")

    return RepositoryDict(
        description=text(repo, "description"),
        fork=fork if isinstance(fork, bool) else None,
        name=text(repo, "name"),
        owner=OwnerDict(
            avatar_url=text(owner, "avatarUrl"),
            name=text(owner, "login"),
            type=text(owner, "__typename"),
            url=text(owner, "url"),
        ),
        stars=stars if isinstance(stars, int) else None,
        url=text(repo, "url"),
    )


def _build_contribution(
    repo: dict[str, object],
    merged: object,
    reviews: object,
    issues: object,
) -> ContributionDict:
    """Map the contribution parts of a GraphQL response onto the output shape."""
    target = _as_dict(_as_dict(repo.get("defaultBranchRef")).get("target"))
    commits = _as_dict(target.get("history")).get("totalCount")
    role = repo.get("viewerPermission")
    dates = [
        date
        for date in (
            _first_date(target.get("latest"), "committedDate"),
            _first_date(merged, "mergedAt"),
        )
        if date
    ]

    return ContributionDict(
        commits=commits if isinstance(commits, int) else 0,
        issues=_issue_count(issues),
        # The newest of the last authored commit and the last merged pull request.
        # Repositories where someone else landed the work have no commit date.
        last_activity_at=max(dates)[:10] if dates else None,
        pull_requests_merged=_issue_count(merged),
        reviews=_issue_count(reviews),
        role=role if isinstance(role, str) else None,
    )


def enrich_batch(
    slugs: list[str], login: str, user_id: str
) -> dict[str, EnrichedRepoDict]:
    """Enrich a batch, splitting it when GitHub times out on the query."""
    try:
        data = github_graphql(build_batch_query(slugs, login), {"userId": user_id})
    except BatchTooLargeError:
        if len(slugs) == 1:
            logger.warning("GitHub cannot answer %s on its own, skipping it", slugs[0])
            return {}

        half = len(slugs) // 2
        logger.info("Splitting a batch of %d repositories in half", len(slugs))
        first = enrich_batch(slugs[:half], login, user_id)
        return first | enrich_batch(slugs[half:], login, user_id)

    enriched: dict[str, EnrichedRepoDict] = {}

    for index, slug in enumerate(slugs):
        repo = data.get(f"repo{index}")

        if not isinstance(repo, dict):
            logger.warning(
                "No data for %s, it may have been deleted or made private", slug
            )
            continue

        enriched[str(repo.get("nameWithOwner") or slug)] = EnrichedRepoDict(
            contribution=_build_contribution(
                repo,
                data.get(f"merged{index}"),
                data.get(f"reviews{index}"),
                data.get(f"issues{index}"),
            ),
            repository=_build_repository(repo),
        )

    return enriched


def enrich_repos(slugs: set[str], login: str, user_id: str) -> list[EnrichedRepoDict]:
    """Enrich every repository, reading from the cache where an entry is still fresh."""
    enriched: dict[str, EnrichedRepoDict] = {}
    pending: list[str] = []

    for slug in sorted(slugs):
        if not REPO_SLUG_PATTERN.match(slug):
            logger.warning("Skipping %s, which is not a valid repository slug", slug)
            continue

        cached = cache_read(f"contribution:{slug}")

        # The cache holds this program's own output, so a shape check suffices.
        # Entries written by an older output shape are dropped and refetched.
        if cached is not None and "contribution" in cached and "repository" in cached:
            enriched[slug] = cast("EnrichedRepoDict", cached)
        else:
            pending.append(slug)

    logger.info("%d repositories cached, %d to fetch", len(enriched), len(pending))

    for start in range(0, len(pending), GRAPHQL_BATCH_SIZE):
        batch = pending[start : start + GRAPHQL_BATCH_SIZE]
        logger.info("Fetching %d of %d", start + len(batch), len(pending))

        for slug, item in enrich_batch(batch, login, user_id).items():
            cache_write(f"contribution:{slug}", dict(item))
            enriched[slug] = item

    # A renamed repository comes back under its current name, so sort at the end.
    return [enriched[slug] for slug in sorted(enriched)]


def main() -> None:
    """Discover and enrich the repositories the configured user contributed to."""
    user_id = fetch_user_id(DEFAULT_PREFIX)
    slugs = (
        discover_contributed_repos(DEFAULT_PREFIX)
        | read_pins_seed(DEFAULT_PREFIX)
        | read_json_seed()
    )
    data = enrich_repos(slugs, DEFAULT_PREFIX, user_id)

    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / "repos.json"
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    logger.info("Wrote %d repositories to %s", len(data), output_path)


if __name__ == "__main__":
    main()
