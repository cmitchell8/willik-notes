#!/usr/bin/env python3
"""
Discover the shipit bot username and catalog its comment formats.

Samples recent PRs to identify the bot author, collects all bot comment
variants, and audits whether formats changed over the analysis period.

Usage:
    export GITHUB_TOKEN=$(gh auth token)
    python discover_shipit_bot.py [--sample-size 50] [--audit-size 200]

Output:
    bot_discovery.json — bot username + catalog of all observed comment templates
"""

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Optional

try:
    import requests
except ImportError:
    print("Error: 'requests' package is required. Install with: pip install requests")
    sys.exit(1)


GRAPHQL_ENDPOINT = "https://api.github.com/graphql"
OUTPUT_FILE = "bot_discovery.json"

SHIPIT_KEYWORDS = [
    "enqueued",
    "has been merged",
    "merge failed",
    "has been cancelled",
    "was not enqueued",
    "error processing",
]

COMMENT_CATEGORIES = {
    "enqueued": ["has been enqueued"],
    "merged": ["has been merged"],
    "failed": ["merge failed"],
    "cancelled": ["has been cancelled"],
    "not_enqueued": ["was not enqueued"],
    "error": ["error processing"],
    "almost_shipit": ["invalid command", "please type `shipit`"],
}

PR_WITH_COMMENTS_QUERY = """
query($owner: String!, $repo: String!, $cursor: String, $prCount: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequests(
      first: $prCount,
      after: $cursor,
      states: [MERGED],
      orderBy: {field: CREATED_AT, direction: DESC}
    ) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        number
        createdAt
        mergedAt
        comments(first: 50) {
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

PR_SPREAD_QUERY = """
query($owner: String!, $repo: String!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequests(
      first: 50,
      after: $cursor,
      states: [MERGED],
      orderBy: {field: CREATED_AT, direction: DESC}
    ) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        number
        createdAt
        mergedAt
        comments(first: 50) {
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
        print("Please set GITHUB_TOKEN or authenticate with: gh auth login")
        sys.exit(1)


def graphql_request(token: str, query: str, variables: dict) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    response = requests.post(
        GRAPHQL_ENDPOINT,
        headers=headers,
        json={"query": query, "variables": variables},
        timeout=30,
    )
    if response.status_code != 200:
        print(f"Error: GitHub API returned status {response.status_code}")
        print(response.text)
        sys.exit(1)
    data = response.json()
    if "errors" in data:
        print(f"GraphQL errors: {json.dumps(data['errors'], indent=2)}")
        sys.exit(1)
    return data


def contains_shipit_keyword(body: str) -> bool:
    body_lower = body.lower()
    return any(kw in body_lower for kw in SHIPIT_KEYWORDS)


def classify_comment(body: str) -> str:
    body_lower = body.lower()
    for category, patterns in COMMENT_CATEGORIES.items():
        if any(p in body_lower for p in patterns):
            return category
    return "unknown"


def get_comment_shape(body: str) -> str:
    """Extract a normalized shape from a comment body for cataloging."""
    lines = body.strip().split("\n")
    first_line = lines[0][:100] if lines else ""
    if len(lines) > 1:
        return f"{first_line} [+{len(lines)-1} lines]"
    return first_line


def discover_bot(token: str, sample_size: int) -> tuple[Optional[str], list[dict]]:
    """Sample recent merged PRs to identify the shipit bot username."""
    print(f"\n{'='*60}")
    print(f"Phase 1: Discovering bot username from {sample_size} recent PRs")
    print(f"{'='*60}")

    bot_candidates: Counter = Counter()
    all_shipit_comments: list[dict] = []

    pages_needed = (sample_size + 49) // 50
    cursor = None

    for page in range(pages_needed):
        batch_size = min(50, sample_size - page * 50)
        data = graphql_request(
            token,
            PR_WITH_COMMENTS_QUERY,
            {
                "owner": "tactivos",
                "repo": "murally",
                "cursor": cursor,
                "prCount": batch_size,
            },
        )

        pr_data = data["data"]["repository"]["pullRequests"]
        prs = pr_data["nodes"]
        page_info = pr_data["pageInfo"]

        for pr in prs:
            for comment in pr["comments"]["nodes"]:
                author = comment.get("author")
                if not author:
                    continue
                login = author["login"]
                body = comment["body"]
                if contains_shipit_keyword(body):
                    bot_candidates[login] += 1
                    all_shipit_comments.append(
                        {
                            "author": login,
                            "body": body,
                            "pr_number": pr["number"],
                            "created_at": comment["createdAt"],
                        }
                    )

        print(f"  Page {page+1}/{pages_needed}: {len(prs)} PRs scanned")

        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]
        time.sleep(0.5)

    if not bot_candidates:
        print("\nNo shipit-related comments found in sampled PRs.")
        return None, []

    print(f"\nBot candidates (by shipit comment count):")
    for login, count in bot_candidates.most_common(5):
        print(f"  {login}: {count} comments")

    bot_username = bot_candidates.most_common(1)[0][0]
    print(f"\nIdentified bot: {bot_username}")

    return bot_username, all_shipit_comments


def audit_format_drift(
    token: str, bot_username: str, audit_size: int
) -> dict[str, Any]:
    """Fetch bot comments spread across the full date range to check for format changes."""
    print(f"\n{'='*60}")
    print(f"Phase 2: Auditing format drift across {audit_size} PRs")
    print(f"{'='*60}")

    all_bot_comments: list[dict] = []
    cursor = None
    fetched = 0
    pages = 0

    while fetched < audit_size:
        data = graphql_request(
            token,
            PR_SPREAD_QUERY,
            {"owner": "tactivos", "repo": "murally", "cursor": cursor},
        )

        pr_data = data["data"]["repository"]["pullRequests"]
        prs = pr_data["nodes"]
        page_info = pr_data["pageInfo"]
        pages += 1

        for pr in prs:
            for comment in pr["comments"]["nodes"]:
                author = comment.get("author")
                if not author:
                    continue
                if author["login"] == bot_username:
                    all_bot_comments.append(
                        {
                            "body": comment["body"],
                            "created_at": comment["createdAt"],
                            "pr_number": pr["number"],
                            "pr_created_at": pr["createdAt"],
                        }
                    )

        fetched += len(prs)
        print(f"  Page {pages}: {len(prs)} PRs scanned, {len(all_bot_comments)} bot comments found so far")

        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]
        time.sleep(0.5)

    print(f"\nTotal bot comments collected: {len(all_bot_comments)}")

    categories: dict[str, list[dict]] = defaultdict(list)
    shapes_by_category: dict[str, Counter] = defaultdict(Counter)
    monthly_category_counts: dict[str, Counter] = defaultdict(Counter)

    for comment in all_bot_comments:
        category = classify_comment(comment["body"])
        shape = get_comment_shape(comment["body"])
        categories[category].append(comment)
        shapes_by_category[category][shape] += 1

        month = comment["created_at"][:7]
        monthly_category_counts[month][category] += 1

    print(f"\nComment categories:")
    for category, comments in sorted(categories.items()):
        print(f"\n  {category} ({len(comments)} comments):")
        for shape, count in shapes_by_category[category].most_common(5):
            print(f"    [{count}x] {shape}")

    unknown_comments = categories.get("unknown", [])
    if unknown_comments:
        print(f"\nUnclassifiable bot comments ({len(unknown_comments)}):")
        for c in unknown_comments[:10]:
            preview = c["body"][:120].replace("\n", " ")
            print(f"  PR #{c['pr_number']}: {preview}")

    sorted_months = sorted(monthly_category_counts.keys())
    format_drift_detected = False
    if len(sorted_months) >= 3:
        early_cats = set(monthly_category_counts[sorted_months[0]].keys())
        late_cats = set(monthly_category_counts[sorted_months[-1]].keys())
        if early_cats != late_cats:
            format_drift_detected = True
            print(f"\nFormat drift detected!")
            print(f"  Early months ({sorted_months[0]}): {early_cats}")
            print(f"  Late months ({sorted_months[-1]}): {late_cats}")
        else:
            print(f"\nNo format drift detected — consistent categories across {len(sorted_months)} months")

    return {
        "total_comments": len(all_bot_comments),
        "categories": {
            cat: {
                "count": len(comments),
                "shapes": dict(shapes_by_category[cat].most_common(10)),
            }
            for cat, comments in categories.items()
        },
        "monthly_counts": {
            month: dict(counts) for month, counts in sorted(monthly_category_counts.items())
        },
        "format_drift_detected": format_drift_detected,
        "unknown_comments": [
            {
                "pr_number": c["pr_number"],
                "body_preview": c["body"][:200],
                "created_at": c["created_at"],
            }
            for c in unknown_comments[:20]
        ],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Discover shipit bot username and catalog comment formats"
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=50,
        help="Number of recent PRs to sample for bot discovery (default: 50)",
    )
    parser.add_argument(
        "--audit-size",
        type=int,
        default=200,
        help="Number of PRs to audit for format drift (default: 200)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=".",
        help="Output directory (default: current directory)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-discovery even if cache exists",
    )
    args = parser.parse_args()

    output_path = os.path.join(args.output_dir, OUTPUT_FILE)

    if os.path.exists(output_path) and not args.force:
        with open(output_path) as f:
            cached = json.load(f)
        print(f"Bot discovery already cached: {output_path}")
        print(f"  Bot username: {cached.get('bot_username')}")
        print(f"  Categories found: {list(cached.get('format_audit', {}).get('categories', {}).keys())}")
        print(f"\nTo re-run, use --force flag.")
        return

    token = get_github_token()
    print(f"GitHub token: ****{token[-4:]}")

    bot_username, initial_comments = discover_bot(token, args.sample_size)
    if not bot_username:
        print("Could not identify bot. Exiting.")
        sys.exit(1)

    format_audit = audit_format_drift(token, bot_username, args.audit_size)

    result = {
        "bot_username": bot_username,
        "discovered_at": datetime.utcnow().isoformat() + "Z",
        "sample_size": args.sample_size,
        "audit_size": args.audit_size,
        "format_audit": format_audit,
    }

    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Discovery complete!")
    print(f"{'='*60}")
    print(f"Bot username: {bot_username}")
    print(f"Output saved to: {output_path}")
    print(f"\nNext step: python fetch_shipit_data.py")


if __name__ == "__main__":
    main()
