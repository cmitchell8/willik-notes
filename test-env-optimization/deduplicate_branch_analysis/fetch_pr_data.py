#!/usr/bin/env python3
"""
Fetch PR data from GitHub repositories using GraphQL API.

This script fetches pull request data from tactivos/murally and tactivos/mural-api
for the specified date range and caches the results to JSON files.

Cached data allows running analyze_branches.py multiple times with different
window parameters without re-fetching from GitHub.

Usage:
    export GITHUB_TOKEN=$(gh auth token)
    python fetch_pr_data.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]
    
    # Skip fetching if cache already exists
    python fetch_pr_data.py  # Will use existing cache if present
    
    # Force re-fetch even if cache exists
    python fetch_pr_data.py --force
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from typing import Any, Optional

try:
    import requests
except ImportError:
    print("Error: 'requests' package is required. Install with: pip install requests")
    sys.exit(1)


GRAPHQL_ENDPOINT = "https://api.github.com/graphql"
REPOS = [
    ("tactivos", "murally"),
    ("tactivos", "mural-api"),
]

# GraphQL query to fetch PRs with pagination
PR_QUERY = """
query($owner: String!, $repo: String!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequests(first: 100, after: $cursor, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo {
        hasNextPage
        endCursor
      }
      totalCount
      nodes {
        number
        headRefName
        createdAt
        closedAt
        mergedAt
        title
        state
      }
    }
  }
}
"""


def get_github_token() -> str:
    """Get GitHub token from environment or gh CLI."""
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token

    # Try to get token from gh CLI
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: No GITHUB_TOKEN found and 'gh auth token' failed.")
        print("Please set GITHUB_TOKEN or authenticate with: gh auth login")
        sys.exit(1)


def fetch_prs(
    owner: str,
    repo: str,
    token: str,
    start_date: datetime,
    end_date: datetime,
) -> list[dict[str, Any]]:
    """Fetch all PRs from a repository within the date range."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    all_prs: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    page = 0

    print(f"\nFetching PRs from {owner}/{repo}...")
    print(f"Date range: {start_date.date()} to {end_date.date()}")

    while True:
        page += 1
        variables = {
            "owner": owner,
            "repo": repo,
            "cursor": cursor,
        }

        response = requests.post(
            GRAPHQL_ENDPOINT,
            headers=headers,
            json={"query": PR_QUERY, "variables": variables},
            timeout=30,
        )

        if response.status_code != 200:
            print(f"Error: GitHub API returned status {response.status_code}")
            print(response.text)
            sys.exit(1)

        data = response.json()

        if "errors" in data:
            print(f"GraphQL errors: {data['errors']}")
            sys.exit(1)

        pr_data = data["data"]["repository"]["pullRequests"]
        nodes = pr_data["nodes"]
        page_info = pr_data["pageInfo"]
        total_count = pr_data["totalCount"]

        if page == 1:
            print(f"Total PRs in repository: {total_count}")

        # Process PRs and filter by date
        prs_in_range = 0
        prs_before_range = 0

        for pr in nodes:
            created_at = datetime.fromisoformat(pr["createdAt"].replace("Z", "+00:00"))
            created_at_naive = created_at.replace(tzinfo=None)

            if created_at_naive > end_date:
                # PR is after our range, skip but continue
                continue
            elif created_at_naive < start_date:
                # PR is before our range, we can stop since ordered by date DESC
                prs_before_range += 1
            else:
                # PR is within our range
                all_prs.append({
                    "number": pr["number"],
                    "branch": pr["headRefName"],
                    "created_at": pr["createdAt"],
                    "closed_at": pr.get("closedAt"),
                    "merged_at": pr.get("mergedAt"),
                    "title": pr["title"],
                    "state": pr["state"],
                })
                prs_in_range += 1

        print(f"  Page {page}: {len(nodes)} PRs fetched, {prs_in_range} in range, {prs_before_range} before range")

        # If we found PRs before our range, we can stop (since ordered by date DESC)
        if prs_before_range > 0:
            print(f"  Reached PRs before {start_date.date()}, stopping pagination")
            break

        # Check if there are more pages
        if not page_info["hasNextPage"]:
            print("  No more pages")
            break

        cursor = page_info["endCursor"]

    print(f"Total PRs fetched in date range: {len(all_prs)}")
    return all_prs


def save_pr_data(prs: list[dict[str, Any]], repo: str, output_dir: str) -> str:
    """Save PR data to a JSON file."""
    filename = f"pr_data_{repo}.json"
    filepath = os.path.join(output_dir, filename)

    output = {
        "repository": repo,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "total_count": len(prs),
        "pull_requests": prs,
    }

    with open(filepath, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Saved to: {filepath}")
    return filepath


def get_cache_filepath(repo: str, output_dir: str) -> str:
    """Get the cache file path for a repository."""
    return os.path.join(output_dir, f"pr_data_{repo}.json")


def check_cache_exists(output_dir: str) -> dict[str, Optional[str]]:
    """Check which cache files exist and return their fetch dates."""
    cache_status = {}
    for _, repo in REPOS:
        filepath = get_cache_filepath(repo, output_dir)
        if os.path.exists(filepath):
            try:
                with open(filepath, "r") as f:
                    data = json.load(f)
                cache_status[repo] = data.get("fetched_at", "unknown")
            except (json.JSONDecodeError, IOError):
                cache_status[repo] = None
        else:
            cache_status[repo] = None
    return cache_status


def main():
    parser = argparse.ArgumentParser(
        description="Fetch PR data from GitHub repositories"
    )
    parser.add_argument(
        "--start",
        type=str,
        default="2025-02-03",
        help="Start date (YYYY-MM-DD), default: 2025-02-03",
    )
    parser.add_argument(
        "--end",
        type=str,
        default="2026-02-03",
        help="End date (YYYY-MM-DD), default: 2026-02-03",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=".",
        help="Output directory for JSON files, default: current directory",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-fetch even if cached data exists",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("PR Data Fetcher")
    print("=" * 60)

    # Check for existing cache
    cache_status = check_cache_exists(args.output_dir)
    all_cached = all(v is not None for v in cache_status.values())

    if all_cached and not args.force:
        print("\nCached data found:")
        for repo, fetched_at in cache_status.items():
            filepath = get_cache_filepath(repo, args.output_dir)
            print(f"  {repo}: {filepath}")
            print(f"    Fetched at: {fetched_at}")

        print("\nTo use this cached data, run analyze_branches.py directly.")
        print("To re-fetch fresh data, run with --force flag.")
        print("\nExample:")
        print("  python analyze_branches.py --window-days 3")
        print("  python analyze_branches.py --window-days 7")
        print("  python fetch_pr_data.py --force  # to refresh cache")
        return

    if args.force and all_cached:
        print("\n--force flag set, re-fetching data...")

    # Parse dates
    start_date = datetime.strptime(args.start, "%Y-%m-%d")
    end_date = datetime.strptime(args.end, "%Y-%m-%d")

    print(f"Date range: {start_date.date()} to {end_date.date()}")

    # Get GitHub token
    token = get_github_token()
    print("GitHub token: ****" + token[-4:])

    # Fetch PRs from both repositories
    for owner, repo in REPOS:
        prs = fetch_prs(owner, repo, token, start_date, end_date)
        save_pr_data(prs, repo, args.output_dir)

    print("\n" + "=" * 60)
    print("Fetch complete! Data cached for future analysis.")
    print("=" * 60)
    print("\nYou can now run analysis with different window sizes:")
    print("  python analyze_branches.py --window-days 3")
    print("  python analyze_branches.py --window-days 5")
    print("  python analyze_branches.py --window-days 7")


if __name__ == "__main__":
    main()
