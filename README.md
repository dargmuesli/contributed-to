# Contributed To - Contribution Data Collector

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![codecov](https://codecov.io/gh/dargmuesli/contributed-to/graph/badge.svg?token=5IX1Z596N8)](https://codecov.io/gh/dargmuesli/contributed-to)
[![CI](https://github.com/dargmuesli/contributed-to/actions/workflows/ci.yml/badge.svg)](https://github.com/dargmuesli/contributed-to/actions/workflows/ci.yml)

Collect the GitHub projects a user contributed to, along with what they actually did in each.

## Why?

A list of projects says nothing about the work that went into them.
This tool reports, per project, how many commits the user authored, how many of their pull requests were merged, how many pull requests they reviewed, how many issues they filed, when they were last active and which permission they hold.

That is what makes [the list of projects contributed to](https://jonas-thelemann.de/projects/contributions) rank and describe its entries instead of listing names.

## Where the list comes from

Three sources are merged, because none of them is complete on its own.

- **The contributions API.**
  `user.repositoriesContributedTo` is the closest thing GitHub offers to the question being asked.
  It does not reach far back, and it leaves out forks on purpose: a branch pushed to your own fork is not a contribution to a project, the pull request it opens upstream is.
- **A JSON seed** at `input/seed.json`, in this tool's own output format.
  Feeding the last run back in means the list never shrinks when the API stops reporting an old contribution.
- **A pins export** at `input/profile-pins.html`, optional.
  GitHub has no API for pinned repositories ([#39589](https://github.com/orgs/community/discussions/39589)), but the pin picker lists everything you ever contributed to, which reaches further back than the API does.
  Export it once, and the JSON seed carries it forward from then on.

Every repository found is then enriched through one batched GraphQL query per eight repositories, at a cost of one rate-limit point each.

## Installation

```bash
uv sync
```

## Quick Start

1. **Configure:**

   ```bash
   export DEFAULT_PREFIX=your-github-username
   export GITHUB_TOKEN=ghp_xxx
   ```

   The token is required, and it has to belong to `DEFAULT_PREFIX`: the GraphQL API rejects unauthenticated requests, and it reports the contribution role as the permission of the token's own account.
   A GitHub App token cannot stand in for it.

2. **Seed it, optionally:** copy a previous `repos.json` to `input/seed.json`, and export the pin picker's HTML to `input/profile-pins.html` on a first run (Profile → Edit pins → DevTools → copy the list markup).

3. **Run:**

   ```bash
   python -m src.main
   ```

4. **Output:** `output/repos.json`

Responses are cached in `github_cache` for a day, so a re-run right after is nearly instant while star counts and contribution counts still refresh daily.

## Docker

```bash
docker build -t dargmuesli/contributed-to .

docker run --rm \
  -e DEFAULT_PREFIX=your-username \
  -e GITHUB_TOKEN=ghp_xxx \
  -v "$(pwd)/input:/srv/app/input:ro" \
  -v "$(pwd)/output:/srv/app/output" \
  -v "$(pwd)/github_cache:/srv/app/github_cache" \
  dargmuesli/contributed-to
```

## Python API

```python
from src.main import discover_contributed_repos, enrich_repos, fetch_user_id

login = "dargmuesli"
user_id = fetch_user_id(login)
projects = enrich_repos(discover_contributed_repos(login), login, user_id)

for project in projects:
    repository = project["repository"]
    contribution = project["contribution"]
    print(f"{repository['name']}: {contribution['commits']} commits")
```

## Output Format

```json
[{
  "contribution": {
    "commits": 6,
    "issues": 15,
    "last_activity_at": "2026-01-29",
    "pull_requests_merged": 6,
    "reviews": 5,
    "role": "TRIAGE"
  },
  "repository": {
    "description": "The full-stack Vue framework.",
    "fork": false,
    "name": "nuxt",
    "owner": {
      "avatar_url": "https://avatars.githubusercontent.com/u/23360933?v=4",
      "name": "nuxt",
      "type": "Organization",
      "url": "https://github.com/nuxt"
    },
    "stars": 60872,
    "url": "https://github.com/nuxt/nuxt"
  }
}]
```

`commits` counts commits authored on the default branch whose author is linked to the account, so read it as a lower bound: work merged into another branch, or committed under an unlinked email address, does not show up.
`role` is the permission the token's account holds today (`ADMIN`, `MAINTAIN`, `WRITE`, `TRIAGE`, `READ`), which is a hint about the relationship rather than a record of it: `READ` does not mean nothing was contributed.

## Development

```bash
# Install development dependencies
uv sync

# Run tests
DEFAULT_PREFIX=testuser pytest

# Code quality
ruff format
ruff check --fix
mypy src tests
```
