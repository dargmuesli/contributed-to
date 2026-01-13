# Contributed To - Profile Pins Extractor

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![codecov](https://codecov.io/gh/dargmuesli/contributed-to/graph/badge.svg?token=5IX1Z596N8)](https://codecov.io/gh/dargmuesli/contributed-to)
[![CI](https://github.com/dargmuesli/contributed-to/actions/workflows/ci.yml/badge.svg)](https://github.com/dargmuesli/contributed-to/actions/workflows/ci.yml)

Extract and enrich GitHub pinned repository data from profile HTML.

## Why?

GitHub doesn't provide an API to fetch pinned repositories ([#39589](https://github.com/orgs/community/discussions/39589)). This tool extracts them from profile HTML and enriches with API metadata.

## Installation

```bash
uv sync
```

## Quick Start

1. **Export pins HTML:** Profile → Edit pins → DevTools (F12) → Copy HTML → Save to `input/profile-pins.html`

2. **Configure:**
   ```bash
   export DEFAULT_PREFIX=your-github-username
   export GITHUB_TOKEN=ghp_xxx  # Optional, for higher rate limits
   ```

3. **Run:**
   ```bash
   python -m src.main
   ```

4. **Output:** `output/repos.json`

## Docker

```bash
docker build -t dargmuesli/contributed-to .

docker run --rm \
  -e DEFAULT_PREFIX=your-username \
  -v "$(pwd)/input:/srv/app/input:ro" \
  -v "$(pwd)/output:/srv/app/output" \
  -v "$(pwd)/github_cache:/srv/app/github_cache" \
  dargmuesli/contributed-to
```

## Python API

```python
from src.main import extract_repos_and_stars

with open("input/profile-pins.html") as f:
    repos = extract_repos_and_stars(f.read())

for item in repos:
    print(f"{item['repository']['name']}: {item['repository']['stars']} stars")
```

## Output Format

```json
[{
  "repository": {
    "name": "repo-name",
    "description": "Description",
    "stars": 1234,
    "fork": false,
    "url": "https://github.com/owner/repo",
    "owner": {
      "name": "owner",
      "type": "User",
      "avatar_url": "https://avatars.githubusercontent.com/u/123",
      "url": "https://github.com/owner"
    }
  }
}]
```

## Development

```bash
# Install development dependencies
uv sync

# Run tests
pytest

# Code quality
ruff format
ruff check --fix
mypy .
```
