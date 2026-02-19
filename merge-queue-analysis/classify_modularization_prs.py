#!/usr/bin/env python3
"""
Classify murally PRs as modularization vs non-modularization work.

  Phase 1: Fetch all merged murally PRs via GraphQL (cached)
  Phase 2: Deterministic bucketing into clear-yes / clear-no / ambiguous
  Phase 3: Fetch file lists for ambiguous PRs via REST API (cached)
  Phase 4: Output ambiguous PRs for review

The worst-case queue impact is computed separately by worst_case_check.py.

Usage:
    source venv/bin/activate

    # Fetch, classify, and output ambiguous PRs
    python classify_modularization_prs.py fetch

    # Force re-fetch of PR metadata
    python classify_modularization_prs.py fetch --refresh

    # Then compute worst-case queue impact
    python worst_case_check.py
"""

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime
from typing import Any

try:
    import requests
except ImportError:
    print("Error: 'requests' package required. Run: pip install requests")
    sys.exit(1)

GRAPHQL_ENDPOINT = "https://api.github.com/graphql"
OWNER = "tactivos"
REPO = "murally"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PR_CACHE = os.path.join(SCRIPT_DIR, "murally_prs_metadata.json")
FILES_CACHE = os.path.join(SCRIPT_DIR, "murally_pr_files_cache.json")

START_DATE = datetime(2025, 2, 18)
END_DATE = datetime(2026, 2, 18)

# ── GitHub API helpers ──────────────────────────────────────────────

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
        print("Error: No GITHUB_TOKEN and 'gh auth token' failed.")
        sys.exit(1)


def graphql_request(token: str, query: str, variables: dict) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    for attempt in range(5):
        resp = requests.post(
            GRAPHQL_ENDPOINT,
            headers=headers,
            json={"query": query, "variables": variables},
            timeout=30,
        )
        if resp.status_code in (502, 503):
            wait = 2 ** (attempt + 1)
            print(f"  Server error {resp.status_code}, retry in {wait}s...")
            time.sleep(wait)
            continue
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            reset = resp.headers.get("X-RateLimit-Reset")
            wait = max(int(reset) - int(time.time()), 10) if reset else 60
            print(f"  Rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        if resp.status_code != 200:
            print(f"Error: GitHub API returned {resp.status_code}")
            print(resp.text[:500])
            sys.exit(1)
        data = resp.json()
        if "errors" in data:
            print(f"GraphQL errors: {json.dumps(data['errors'], indent=2)}")
            sys.exit(1)
        return data
    print("Failed after retries")
    sys.exit(1)


def rest_request(token: str, url: str) -> Any:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    for attempt in range(5):
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code in (502, 503):
            time.sleep(2 ** (attempt + 1))
            continue
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            reset = resp.headers.get("X-RateLimit-Reset")
            wait = max(int(reset) - int(time.time()), 10) if reset else 60
            print(f"  Rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        if resp.status_code != 200:
            print(f"  REST error {resp.status_code} for {url}")
            return None
        return resp.json()
    return None


# ── Phase 1: Fetch all merged murally PRs ───────────────────────────

MERGED_PRS_QUERY = """
query($owner: String!, $repo: String!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequests(
      first: 100,
      after: $cursor,
      states: [MERGED],
      orderBy: {field: CREATED_AT, direction: DESC}
    ) {
      pageInfo { hasNextPage endCursor }
      totalCount
      nodes {
        number
        title
        headRefName
        mergedAt
        changedFiles
        additions
        deletions
        body
        labels(first: 10) {
          nodes { name }
        }
      }
    }
  }
}
"""


def fetch_all_merged_prs(token: str) -> list[dict]:
    print("Phase 1: Fetching all merged PRs from tactivos/murally...")
    all_prs = []
    cursor = None
    page = 0

    while True:
        page += 1
        data = graphql_request(token, MERGED_PRS_QUERY, {
            "owner": OWNER, "repo": REPO, "cursor": cursor,
        })
        pr_data = data["data"]["repository"]["pullRequests"]
        nodes = pr_data["nodes"]
        page_info = pr_data["pageInfo"]

        if page == 1:
            print(f"  Total merged PRs in repo: {pr_data['totalCount']}")

        done = False
        for pr in nodes:
            merged_at = pr.get("mergedAt")
            if not merged_at:
                continue
            merged_dt = datetime.fromisoformat(merged_at.replace("Z", "+00:00")).replace(tzinfo=None)
            if merged_dt > END_DATE:
                continue
            if merged_dt < START_DATE:
                done = True
                break

            labels = [l["name"] for l in pr.get("labels", {}).get("nodes", [])]
            all_prs.append({
                "number": pr["number"],
                "title": pr["title"],
                "branch": pr["headRefName"],
                "merged_at": pr["mergedAt"],
                "changed_files": pr["changedFiles"],
                "additions": pr["additions"],
                "deletions": pr["deletions"],
                "body": (pr.get("body") or "")[:2000],
                "labels": labels,
            })

        print(f"  Page {page}: {len(nodes)} PRs fetched, {len(all_prs)} in range so far")

        if done or not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]
        time.sleep(0.3)

    all_prs.sort(key=lambda p: p["merged_at"], reverse=True)
    print(f"  Total PRs in date range: {len(all_prs)}")
    return all_prs


# ── Phase 2: Deterministic bucketing ────────────────────────────────

JIRA_FEATURE_PREFIXES = [
    "can-", "cwi-", "ecomm-", "engage-", "play-", "iam-",
    "esc-", "bc-", "scqm-", "collab-", "ai-", "auth-",
    "plat-", "infra-", "sre-", "data-", "dx-",
]

MODULARIZATION_KEYWORDS = [
    "modulariz", "move to package", "extract to package",
    "move-to-package", "extract-to-package", "split package",
    "reorganize package", "migrate to package",
    "create package", "new package",
]

MODULARIZATION_BRANCH_STRONG = [
    r"^move/",
    r"^modularize-",
    r"^move-to-package",
    r"^create/package-",
    r"^extract/",
]

MODULARIZATION_BRANCH_AMBIGUOUS = [
    r"^next-",
    r"^fix/next-",
    r"^add/next-",
    r"^update/next-",
    r"^migrate-",
    r"^migrate/",
    r"^fix/migrate-",
    r"^update/migrate-",
]


def text_has_modularization_keyword(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in MODULARIZATION_KEYWORDS)


def branch_matches_strong_modularization(branch: str) -> bool:
    return any(re.match(pat, branch, re.IGNORECASE) for pat in MODULARIZATION_BRANCH_STRONG)


def branch_matches_ambiguous(branch: str) -> bool:
    return any(re.match(pat, branch, re.IGNORECASE) for pat in MODULARIZATION_BRANCH_AMBIGUOUS)


def has_jira_feature_prefix(text: str) -> bool:
    text_lower = text.lower()
    return any(prefix in text_lower for prefix in JIRA_FEATURE_PREFIXES)


def classify_pr(pr: dict) -> str:
    """Returns 'yes', 'no', or 'ambiguous'."""
    title = pr["title"]
    branch = pr["branch"]
    body = pr.get("body", "")
    changed_files = pr["changed_files"]
    title_lower = title.lower()
    branch_lower = branch.lower()

    # ── Clear YES ──
    if branch_matches_strong_modularization(branch):
        return "yes"
    if text_has_modularization_keyword(title):
        return "yes"
    if text_has_modularization_keyword(body) and ("packages/" in body or "packages/" in title_lower):
        return "yes"

    # ── Clear NO ──
    # Jira feature tickets with no modularization signal
    jira_in_title = has_jira_feature_prefix(title)
    jira_in_branch = has_jira_feature_prefix(branch)
    mod_in_body = text_has_modularization_keyword(body)
    if (jira_in_title or jira_in_branch) and not mod_in_body and not branch_matches_ambiguous(branch):
        return "no"
    # Small PRs with no modularization keywords
    if changed_files <= 3 and not text_has_modularization_keyword(title + " " + body + " " + branch):
        return "no"
    # Obvious fix/feature patterns with no modularization context
    non_mod_prefixes = [
        "fix/", "hotfix/", "bugfix/", "chore/", "docs/", "ci/",
        "test/", "revert/", "release/", "bump/", "dependabot/",
    ]
    if any(branch_lower.startswith(p) for p in non_mod_prefixes):
        if not branch_matches_ambiguous(branch) and not text_has_modularization_keyword(title + " " + body):
            return "no"
    # update/ branches with jira or no mod signal
    if branch_lower.startswith("update/") and not branch_matches_ambiguous(branch):
        if not text_has_modularization_keyword(title + " " + body):
            return "no"
    # add/ branches with jira
    if branch_lower.startswith("add/") and not branch_matches_ambiguous(branch):
        if jira_in_branch and not mod_in_body:
            return "no"
        if not text_has_modularization_keyword(title + " " + body):
            return "no"
    # remove/ branches
    if branch_lower.startswith("remove/") and not text_has_modularization_keyword(title + " " + body):
        return "no"

    # ── Ambiguous ──
    return "ambiguous"


def bucket_prs(prs: list[dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {"yes": [], "no": [], "ambiguous": []}
    for pr in prs:
        bucket = classify_pr(pr)
        pr["bucket"] = bucket
        buckets[bucket].append(pr)

    print(f"\nPhase 2: Deterministic bucketing results:")
    print(f"  Clear YES (modularization):  {len(buckets['yes'])}")
    print(f"  Clear NO  (not modulariz.):  {len(buckets['no'])}")
    print(f"  AMBIGUOUS (needs review):    {len(buckets['ambiguous'])}")
    return buckets


# ── Phase 3: Fetch file lists for ambiguous PRs ────────────────────

def fetch_pr_files(token: str, pr_number: int) -> list[str]:
    """Fetch changed file paths for a PR via REST API."""
    files = []
    page = 1
    while True:
        url = f"https://api.github.com/repos/{OWNER}/{REPO}/pulls/{pr_number}/files?per_page=100&page={page}"
        data = rest_request(token, url)
        if not data:
            break
        for f in data:
            files.append(f.get("filename", ""))
        if len(data) < 100:
            break
        page += 1
        time.sleep(0.2)
    return files


def fetch_files_for_prs(token: str, pr_numbers: list[int], cache: dict) -> dict:
    """Fetch files for a list of PRs, using cache."""
    to_fetch = [n for n in pr_numbers if str(n) not in cache]
    if to_fetch:
        print(f"  Fetching files for {len(to_fetch)} PRs ({len(pr_numbers) - len(to_fetch)} cached)...")
        for i, num in enumerate(to_fetch, 1):
            files = fetch_pr_files(token, num)
            cache[str(num)] = files
            if i % 20 == 0:
                print(f"    {i}/{len(to_fetch)} done...")
                with open(FILES_CACHE, "w") as f:
                    json.dump(cache, f)
            time.sleep(0.3)
        with open(FILES_CACHE, "w") as f:
            json.dump(cache, f)
        print(f"  Done fetching files.")
    return cache


# ── Phase 4: Output ambiguous PRs for review ────────────────────────

def output_ambiguous_for_review(ambiguous: list[dict], files_cache: dict):
    """Print ambiguous PRs in a format suitable for manual review."""
    print(f"\n{'='*70}")
    print(f"AMBIGUOUS PRs for review ({len(ambiguous)} total)")
    print(f"{'='*70}")
    print()

    for pr in ambiguous:
        files = files_cache.get(str(pr["number"]), [])
        pkg_dirs = set()
        for f in files:
            if f.startswith("packages/"):
                parts = f.split("/")
                if len(parts) >= 2:
                    pkg_dirs.add(parts[1])

        print(f"--- PR #{pr['number']} ---")
        print(f"  Title:    {pr['title']}")
        print(f"  Branch:   {pr['branch']}")
        print(f"  Merged:   {pr['merged_at'][:10]}")
        print(f"  Files:    {pr['changed_files']} changed (+{pr['additions']}/-{pr['deletions']})")
        if pkg_dirs:
            print(f"  Packages: {', '.join(sorted(pkg_dirs))} ({len(pkg_dirs)} packages)")
        if files:
            sample = files[:10]
            print(f"  Files (sample): {', '.join(sample)}")
            if len(files) > 10:
                print(f"    ... and {len(files) - 10} more")
        body_preview = pr.get("body", "")[:300].replace("\n", " ").strip()
        if body_preview:
            print(f"  Body:     {body_preview}")
        print()

    # Also write JSON for easier consumption
    review_file = os.path.join(SCRIPT_DIR, "ambiguous_prs_for_review.json")
    review_data = []
    for pr in ambiguous:
        files = files_cache.get(str(pr["number"]), [])
        pkg_dirs = set()
        for f in files:
            if f.startswith("packages/"):
                parts = f.split("/")
                if len(parts) >= 2:
                    pkg_dirs.add(parts[1])
        review_data.append({
            "number": pr["number"],
            "title": pr["title"],
            "branch": pr["branch"],
            "merged_at": pr["merged_at"][:10],
            "changed_files": pr["changed_files"],
            "additions": pr["additions"],
            "deletions": pr["deletions"],
            "packages_touched": sorted(pkg_dirs),
            "files_sample": files[:20],
            "total_files": len(files),
            "body_preview": pr.get("body", "")[:500],
        })
    with open(review_file, "w") as f:
        json.dump(review_data, f, indent=2)
    print(f"\nAmbiguous PRs written to: {review_file}")



# ── CLI ─────────────────────────────────────────────────────────────

def cmd_fetch(args):
    token = get_github_token()

    # Phase 1: Fetch PRs
    if os.path.exists(PR_CACHE) and not args.refresh:
        print(f"Phase 1: Loading cached PR data from {PR_CACHE}")
        with open(PR_CACHE) as f:
            prs = json.load(f)
        print(f"  {len(prs)} PRs loaded from cache")
    else:
        prs = fetch_all_merged_prs(token)
        with open(PR_CACHE, "w") as f:
            json.dump(prs, f, indent=2)
        print(f"  Saved to {PR_CACHE}")

    # Phase 2: Bucket
    buckets = bucket_prs(prs)

    # Phase 3: Fetch files for ambiguous PRs
    files_cache: dict = {}
    if os.path.exists(FILES_CACHE):
        with open(FILES_CACHE) as f:
            files_cache = json.load(f)

    ambiguous_numbers = [p["number"] for p in buckets["ambiguous"]]
    # Also sample some clear-NO PRs for validation
    no_sample_size = min(30, len(buckets["no"]))
    random.seed(42)
    no_sample = random.sample(buckets["no"], no_sample_size)
    no_sample_numbers = [p["number"] for p in no_sample]

    all_to_fetch = ambiguous_numbers + no_sample_numbers
    print(f"\nPhase 3: Fetching files for {len(ambiguous_numbers)} ambiguous + {len(no_sample_numbers)} validation PRs")
    files_cache = fetch_files_for_prs(token, all_to_fetch, files_cache)

    # Phase 4: Output ambiguous for review
    output_ambiguous_for_review(buckets["ambiguous"], files_cache)

    # Summary
    print(f"\n{'='*70}")
    print("NEXT STEPS:")
    print(f"  1. Review ambiguous PRs (printed above or in ambiguous_prs_for_review.json)")
    print(f"  2. Run: python worst_case_check.py  (worst-case queue impact)")
    print(f"  3. Run: python validate_classification.py  (export for manual validation)")
    print(f"{'='*70}")


def main():
    parser = argparse.ArgumentParser(description="Classify murally modularization PRs")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    fetch_parser = subparsers.add_parser("fetch", help="Fetch PRs and classify into buckets")
    fetch_parser.add_argument("--refresh", action="store_true", help="Force re-fetch PR metadata")

    args = parser.parse_args()
    if args.command == "fetch":
        cmd_fetch(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
