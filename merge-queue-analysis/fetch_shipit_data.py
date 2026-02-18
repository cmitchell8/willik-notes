#!/usr/bin/env python3
"""
Fetch merged PR data with shipit bot comments from GitHub GraphQL API.

Collects ALL comments from the identified bot author on merged/closed PRs
across all repos in the mural-web shipit queue. Classification is done
after collection so heuristics can be refined without re-fetching.

Usage:
    export GITHUB_TOKEN=$(gh auth token)
    python fetch_shipit_data.py [--repos murally mural-api] [--start-date 2025-02-18]

Prerequisites:
    Run discover_shipit_bot.py first to produce bot_discovery.json.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from typing import Any, Optional

try:
    import requests
except ImportError:
    print("Error: 'requests' package is required. Install with: pip install requests")
    sys.exit(1)


GRAPHQL_ENDPOINT = "https://api.github.com/graphql"
OWNER = "tactivos"

ALL_MURAL_WEB_REPOS = [
    "antivirus",
    "e2e-mural-tests",
    "haproxy-realtime",
    "mongo-seeder",
    "mural-api",
    "mural-integrations",
    "mural-integrations-webex",
    "mural-lb",
    "mural-notifier",
    "mural-realtime",
    "mural-render",
    "mural-upload",
    "murally",
    "pdf-import",
    "mural-seeder",
]

PR_WITH_COMMENTS_QUERY = """
query($owner: String!, $repo: String!, $cursor: String, $states: [PullRequestState!]!) {
  repository(owner: $owner, name: $repo) {
    pullRequests(
      first: 50,
      after: $cursor,
      states: $states,
      orderBy: {field: CREATED_AT, direction: DESC}
    ) {
      pageInfo {
        hasNextPage
        endCursor
      }
      totalCount
      nodes {
        number
        headRefName
        createdAt
        mergedAt
        closedAt
        state
        comments(first: 100) {
          pageInfo {
            hasNextPage
            endCursor
          }
          nodes {
            author {
              login
            }
            body
            createdAt
          }
        }
      }
    }
  }
}
"""

ADDITIONAL_COMMENTS_QUERY = """
query($owner: String!, $repo: String!, $prNumber: Int!, $cursor: String!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $prNumber) {
      comments(first: 100, after: $cursor) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          author {
            login
          }
          body
          createdAt
        }
      }
    }
  }
}
"""


def get_github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: No GITHUB_TOKEN found and 'gh auth token' failed.")
        sys.exit(1)


def graphql_request(token: str, query: str, variables: dict) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    for attempt in range(3):
        response = requests.post(
            GRAPHQL_ENDPOINT,
            headers=headers,
            json={"query": query, "variables": variables},
            timeout=30,
        )
        if response.status_code == 502 or response.status_code == 503:
            wait = 2 ** (attempt + 1)
            print(f"  Server error {response.status_code}, retrying in {wait}s...")
            time.sleep(wait)
            continue
        if response.status_code != 200:
            print(f"Error: GitHub API returned status {response.status_code}")
            print(response.text)
            sys.exit(1)
        data = response.json()
        if "errors" in data:
            print(f"GraphQL errors: {json.dumps(data['errors'], indent=2)}")
            sys.exit(1)
        return data
    print("Failed after 3 retries")
    sys.exit(1)


def classify_comment(body: str) -> str:
    body_lower = body.lower()
    if "has been enqueued" in body_lower or "has been enqueued" in body_lower:
        return "enqueued"
    if "has been merged" in body_lower:
        return "merged"
    if "merge failed" in body_lower:
        return "failed"
    if "has been cancelled" in body_lower:
        return "cancelled"
    if "was not enqueued" in body_lower:
        return "not_enqueued"
    if "error processing" in body_lower:
        return "error"
    if "invalid command" in body_lower and "shipit" in body_lower:
        return "almost_shipit"
    return "unknown"


def parse_effort_prs(body: str) -> list[dict[str, Any]]:
    """Extract linked PRs from an 'enqueued' comment body."""
    prs = []
    pattern = r"https://github\.com/([^/]+/[^/]+)/pull/(\d+)"
    for match in re.finditer(pattern, body):
        repo_full = match.group(1)
        pr_number = int(match.group(2))
        prs.append({"repository": repo_full, "pr_number": pr_number})
    return prs


def classify_and_extract_events(
    bot_comments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Classify each bot comment into a shipit event."""
    events = []
    for comment in bot_comments:
        event_type = classify_comment(comment["body"])
        event: dict[str, Any] = {
            "type": event_type,
            "timestamp": comment["created_at"],
        }
        if event_type == "enqueued":
            event["effort_prs"] = parse_effort_prs(comment["body"])
        if event_type == "unknown":
            event["body_preview"] = comment["body"][:200]
        events.append(event)
    return events


def fetch_additional_comments(
    token: str, owner: str, repo: str, pr_number: int, cursor: str
) -> list[dict]:
    """Fetch remaining comments for a PR that has >100 comments."""
    all_comments = []
    current_cursor = cursor

    while current_cursor:
        data = graphql_request(
            token,
            ADDITIONAL_COMMENTS_QUERY,
            {
                "owner": owner,
                "repo": repo,
                "prNumber": pr_number,
                "cursor": current_cursor,
            },
        )
        comments_data = data["data"]["repository"]["pullRequest"]["comments"]
        all_comments.extend(
            {
                "author": c["author"]["login"] if c.get("author") else None,
                "body": c["body"],
                "created_at": c["createdAt"],
            }
            for c in comments_data["nodes"]
        )
        if comments_data["pageInfo"]["hasNextPage"]:
            current_cursor = comments_data["pageInfo"]["endCursor"]
            time.sleep(0.3)
        else:
            break

    return all_comments


def fetch_repo_data(
    token: str,
    repo: str,
    bot_username: str,
    start_date: datetime,
    end_date: datetime,
) -> dict[str, Any]:
    """Fetch all merged + closed PRs with bot comments for a repo."""
    print(f"\nFetching {OWNER}/{repo}...")
    print(f"  Date range: {start_date.date()} to {end_date.date()}")

    all_prs: list[dict[str, Any]] = []

    for state_filter in [["MERGED"], ["CLOSED"]]:
        state_label = state_filter[0].lower()
        cursor = None
        page = 0
        prs_in_range = 0
        done = False

        while not done:
            page += 1
            data = graphql_request(
                token,
                PR_WITH_COMMENTS_QUERY,
                {
                    "owner": OWNER,
                    "repo": repo,
                    "cursor": cursor,
                    "states": state_filter,
                },
            )

            pr_data = data["data"]["repository"]["pullRequests"]
            prs = pr_data["nodes"]
            page_info = pr_data["pageInfo"]

            if page == 1:
                print(f"  [{state_label}] Total in repo: {pr_data['totalCount']}")

            for pr in prs:
                created_at = datetime.fromisoformat(
                    pr["createdAt"].replace("Z", "+00:00")
                ).replace(tzinfo=None)

                if created_at > end_date:
                    continue
                if created_at < start_date:
                    done = True
                    break

                bot_comments = []
                for comment in pr["comments"]["nodes"]:
                    author = comment.get("author")
                    if not author:
                        continue
                    if author["login"] == bot_username:
                        bot_comments.append(
                            {
                                "body": comment["body"],
                                "created_at": comment["createdAt"],
                                "author": author["login"],
                            }
                        )

                if pr["comments"]["pageInfo"]["hasNextPage"]:
                    extra_comments = fetch_additional_comments(
                        token,
                        OWNER,
                        repo,
                        pr["number"],
                        pr["comments"]["pageInfo"]["endCursor"],
                    )
                    for c in extra_comments:
                        if c["author"] == bot_username:
                            bot_comments.append(c)

                if not bot_comments and state_filter == ["CLOSED"]:
                    continue

                shipit_events = classify_and_extract_events(bot_comments)

                pr_record = {
                    "number": pr["number"],
                    "branch": pr["headRefName"],
                    "created_at": pr["createdAt"],
                    "merged_at": pr.get("mergedAt"),
                    "closed_at": pr.get("closedAt"),
                    "state": pr["state"],
                    "bot_comments": bot_comments,
                    "shipit_events": shipit_events,
                }
                all_prs.append(pr_record)
                prs_in_range += 1

            print(
                f"  [{state_label}] Page {page}: {len(prs)} PRs, {prs_in_range} with bot comments in range"
            )

            if done or not page_info["hasNextPage"]:
                break
            cursor = page_info["endCursor"]
            time.sleep(0.5)

    all_prs.sort(key=lambda p: p["created_at"], reverse=True)

    prs_with_events = sum(1 for p in all_prs if p["shipit_events"])
    total_events = sum(len(p["shipit_events"]) for p in all_prs)
    unknown_events = sum(
        1
        for p in all_prs
        for e in p["shipit_events"]
        if e["type"] == "unknown"
    )

    print(f"  Summary: {len(all_prs)} PRs, {prs_with_events} with shipit events, {total_events} total events ({unknown_events} unknown)")

    return {
        "repository": repo,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "bot_username": bot_username,
        "date_range": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "pull_requests": all_prs,
    }


def get_cache_path(repo: str, output_dir: str) -> str:
    return os.path.join(output_dir, f"shipit_data_{repo}.json")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch PR data with shipit bot comments from GitHub"
    )
    parser.add_argument(
        "--repos",
        nargs="+",
        default=None,
        help=f"Repos to fetch (default: all {len(ALL_MURAL_WEB_REPOS)} mural-web repos)",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default="2025-02-18",
        help="Start date YYYY-MM-DD (default: 2025-02-18)",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default="2026-02-18",
        help="End date YYYY-MM-DD (default: 2026-02-18)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=".",
        help="Output directory (default: current directory)",
    )
    parser.add_argument(
        "--bot-username",
        type=str,
        default=None,
        help="Override bot username (default: read from bot_discovery.json)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-fetch even if cached data exists",
    )
    args = parser.parse_args()

    repos = args.repos or ALL_MURAL_WEB_REPOS
    start_date = datetime.strptime(args.start_date, "%Y-%m-%d")
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d")

    bot_username = args.bot_username
    if not bot_username:
        discovery_path = os.path.join(args.output_dir, "bot_discovery.json")
        if not os.path.exists(discovery_path):
            print(f"Error: {discovery_path} not found.")
            print("Run discover_shipit_bot.py first, or use --bot-username.")
            sys.exit(1)
        with open(discovery_path) as f:
            discovery = json.load(f)
        bot_username = discovery["bot_username"]

    print("=" * 60)
    print("Shipit Data Fetcher")
    print("=" * 60)
    print(f"Bot username: {bot_username}")
    print(f"Date range: {start_date.date()} to {end_date.date()}")
    print(f"Repos: {', '.join(repos)}")

    if not args.force:
        cached = []
        for repo in repos:
            path = get_cache_path(repo, args.output_dir)
            if os.path.exists(path):
                cached.append(repo)
        if cached:
            print(f"\nCached data exists for: {', '.join(cached)}")
            uncached = [r for r in repos if r not in cached]
            if uncached:
                print(f"Will fetch: {', '.join(uncached)}")
                repos = uncached
            else:
                print("All repos cached. Use --force to re-fetch.")
                return

    token = get_github_token()
    print(f"GitHub token: ****{token[-4:]}")

    for repo in repos:
        data = fetch_repo_data(token, repo, bot_username, start_date, end_date)
        cache_path = get_cache_path(repo, args.output_dir)
        with open(cache_path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"  Saved to: {cache_path}")
        time.sleep(1)

    print(f"\n{'='*60}")
    print("Fetch complete!")
    print("=" * 60)
    print(f"\nCached files in {args.output_dir}:")
    for repo in (args.repos or ALL_MURAL_WEB_REPOS):
        path = get_cache_path(repo, args.output_dir)
        if os.path.exists(path):
            size_kb = os.path.getsize(path) / 1024
            print(f"  {os.path.basename(path)} ({size_kb:.1f} KB)")
    print(f"\nNext step: python analyze_queue_timeline.py")


if __name__ == "__main__":
    main()
