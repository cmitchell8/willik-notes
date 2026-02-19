#!/usr/bin/env python3
"""
Classify murally PRs as modularization vs non-modularization work.

Hybrid approach:
  Phase 1: Fetch all merged murally PRs via GraphQL (cached)
  Phase 2: Deterministic bucketing into clear-yes / clear-no / ambiguous
  Phase 3: Fetch file lists for ambiguous PRs via REST API (cached)
  Phase 4: Output ambiguous PRs for interactive LLM review
  Phase 5: Merge LLM decisions from JSON file
  Phase 6: Cross-reference with shipit queue data and generate report

Usage:
    # Activate venv first
    source venv/bin/activate

    # Phase 1-4: fetch, bucket, and output ambiguous PRs
    python classify_modularization_prs.py fetch

    # Phase 5-6: after LLM review, generate report
    python classify_modularization_prs.py report

    # Force re-fetch of PR metadata
    python classify_modularization_prs.py fetch --refresh
"""

import argparse
import csv
import json
import os
import random
import re
import subprocess
import sys
import time
from collections import defaultdict
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
LLM_DECISIONS = os.path.join(SCRIPT_DIR, "modularization_llm_decisions.json")
QUEUE_EVENTS = os.path.join(SCRIPT_DIR, "queue_events.csv")
OUTPUT_DOC = os.path.join(SCRIPT_DIR, "modularization-pr-analysis.md")

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


# ── Phase 4: Output ambiguous PRs for LLM review ───────────────────

def output_ambiguous_for_review(ambiguous: list[dict], files_cache: dict):
    """Print ambiguous PRs in a format suitable for LLM batch review."""
    print(f"\n{'='*70}")
    print(f"AMBIGUOUS PRs for LLM review ({len(ambiguous)} total)")
    print(f"{'='*70}")
    print(f"\nClassify each as: modularization | not_modularization | modularization_adjacent")
    print(f"(modularization_adjacent = fixes/follow-ups caused by modularization)")
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
    print(f"After LLM review, save decisions to: {LLM_DECISIONS}")
    print(f"Format: {{\"<pr_number>\": \"modularization\" | \"not_modularization\" | \"modularization_adjacent\", ...}}")


# ── Phase 5-6: Report generation ────────────────────────────────────

def load_queue_events() -> list[dict]:
    events = []
    with open(QUEUE_EVENTS, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            events.append(row)
    return events


def generate_report(prs: list[dict], llm_decisions: dict):
    """Generate the final analysis document."""
    # Merge LLM decisions into PR classification
    final_classification: dict[int, str] = {}
    for pr in prs:
        num = pr["number"]
        bucket = pr.get("bucket", "ambiguous")
        if bucket == "yes":
            final_classification[num] = "modularization"
        elif bucket == "no":
            final_classification[num] = "not_modularization"
        else:
            llm = llm_decisions.get(str(num))
            if llm:
                final_classification[num] = llm
            else:
                final_classification[num] = "not_modularization"

    mod_prs = [p for p in prs if final_classification[p["number"]] == "modularization"]
    adjacent_prs = [p for p in prs if final_classification[p["number"]] == "modularization_adjacent"]
    not_mod_prs = [p for p in prs if final_classification[p["number"]] == "not_modularization"]

    # Load queue events
    queue_events = load_queue_events()
    murally_events = [e for e in queue_events if e["repo"] == "murally"]
    total_events = len(queue_events)
    total_murally = len(murally_events)

    # Cross-reference
    mod_pr_numbers = {p["number"] for p in mod_prs}
    adjacent_pr_numbers = {p["number"] for p in adjacent_prs}
    mod_queue_events = [e for e in murally_events if int(e["pr_number"]) in mod_pr_numbers]
    adjacent_queue_events = [e for e in murally_events if int(e["pr_number"]) in adjacent_pr_numbers]

    # Recomputed stats
    mod_event_count = len(mod_queue_events)
    adjacent_event_count = len(adjacent_queue_events)
    combined_event_count = mod_event_count + adjacent_event_count

    murally_ex_mod = total_murally - mod_event_count
    murally_ex_all = total_murally - combined_event_count
    total_ex_mod = total_events - mod_event_count
    total_ex_all = total_events - combined_event_count

    mural_api_events = len([e for e in queue_events if e["repo"] == "mural-api"])
    effort_events = len([e for e in queue_events if e.get("is_effort") == "True"])

    # Monthly breakdown
    monthly: dict[str, int] = defaultdict(int)
    for pr in mod_prs:
        month = pr["merged_at"][:7]
        monthly[month] += 1
    for pr in adjacent_prs:
        month = pr["merged_at"][:7]
        monthly[month] += 1

    # Deterministic bucket stats
    yes_count = len([p for p in prs if p.get("bucket") == "yes"])
    no_count = len([p for p in prs if p.get("bucket") == "no"])
    ambiguous_count = len([p for p in prs if p.get("bucket") == "ambiguous"])
    llm_mod = len([p for p in prs if p.get("bucket") == "ambiguous" and llm_decisions.get(str(p["number"])) == "modularization"])
    llm_adj = len([p for p in prs if p.get("bucket") == "ambiguous" and llm_decisions.get(str(p["number"])) == "modularization_adjacent"])
    llm_not = len([p for p in prs if p.get("bucket") == "ambiguous" and llm_decisions.get(str(p["number"])) == "not_modularization"])
    llm_unreviewed = ambiguous_count - llm_mod - llm_adj - llm_not

    # Build report
    lines = []
    lines.append("# Modularization PR Analysis: Impact on Merge Queue Data\n")
    lines.append(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n")

    lines.append("## Summary\n")
    lines.append(f"**{len(mod_prs)}** out of **{len(prs)}** merged murally PRs "
                  f"(**{len(mod_prs)/len(prs)*100:.1f}%**) were modularization work "
                  f"in the period {START_DATE.strftime('%b %Y')} – {END_DATE.strftime('%b %Y')}.\n")
    if adjacent_prs:
        lines.append(f"An additional **{len(adjacent_prs)}** PRs were modularization-adjacent "
                      f"(fixes/follow-ups caused by modularization).\n")
    lines.append(f"These accounted for **{mod_event_count}** shipit queue events "
                  f"(**{mod_event_count/total_murally*100:.1f}%** of murally's {total_murally} events).\n")

    lines.append("## Methodology\n")
    lines.append("### Hybrid deterministic + LLM classification\n")
    lines.append(f"1. **Fetched** all {len(prs)} merged PRs from `tactivos/murally` "
                  f"({START_DATE.strftime('%Y-%m-%d')} to {END_DATE.strftime('%Y-%m-%d')}) via GitHub GraphQL API\n")
    lines.append(f"2. **Deterministic bucketing** using branch name, title, and body keyword rules:\n")
    lines.append(f"   - Clear YES (strong modularization signals): **{yes_count}** PRs\n")
    lines.append(f"   - Clear NO (obvious feature/fix/chore work): **{no_count}** PRs\n")
    lines.append(f"   - Ambiguous (needs human review): **{ambiguous_count}** PRs\n")
    lines.append(f"3. **File-level analysis** fetched for all ambiguous PRs via REST API\n")
    lines.append(f"4. **LLM interactive review** of ambiguous PRs examining title, branch, body, and file lists:\n")
    lines.append(f"   - Classified as modularization: **{llm_mod}**\n")
    lines.append(f"   - Classified as modularization-adjacent: **{llm_adj}**\n")
    lines.append(f"   - Classified as not modularization: **{llm_not}**\n")
    if llm_unreviewed > 0:
        lines.append(f"   - Unreviewed (defaulted to not-modularization): **{llm_unreviewed}**\n")
    lines.append(f"5. **Conservative approach**: when in doubt, classified as NOT modularization\n")

    lines.append("\n### Classification signals\n")
    lines.append("**Clear YES signals:**\n")
    lines.append("- Branch starts with `move/`, `modularize-`, `create/package-`, `extract/`\n")
    lines.append("- Title/body contains \"modularization\", \"move to package\", \"extract to package\"\n")
    lines.append("\n**Clear NO signals:**\n")
    lines.append("- Jira feature ticket prefixes (CAN-, CWI-, ECOMM-, etc.) with no modularization keywords\n")
    lines.append("- Small PRs (≤3 files) with no modularization keywords\n")
    lines.append("- Standard fix/chore/docs/test branches with no modularization context\n")

    lines.append("\n## Monthly Breakdown\n")
    lines.append("| Month | Modularization PRs | % of that month's murally PRs |\n")
    lines.append("|-------|-------------------|-------------------------------|\n")

    monthly_total: dict[str, int] = defaultdict(int)
    for pr in prs:
        month = pr["merged_at"][:7]
        monthly_total[month] += 1

    for month in sorted(monthly.keys()):
        count = monthly[month]
        total = monthly_total.get(month, 1)
        pct = count / total * 100
        lines.append(f"| {month} | {count} | {pct:.1f}% |\n")
    lines.append(f"| **Total** | **{len(mod_prs) + len(adjacent_prs)}** | "
                  f"**{(len(mod_prs) + len(adjacent_prs))/len(prs)*100:.1f}%** |\n")

    lines.append("\n## Impact on Merge Queue Analysis\n")
    lines.append("### Recomputed queue statistics\n")
    lines.append("| Metric | Original | Excl. modularization | Excl. mod + adjacent |\n")
    lines.append("|--------|----------|---------------------|---------------------|\n")

    def pct(n: int, d: int) -> str:
        return f"{n/d*100:.1f}%" if d > 0 else "N/A"

    lines.append(f"| Total queue events | {total_events:,} | {total_ex_mod:,} | {total_ex_all:,} |\n")
    lines.append(f"| murally events | {total_murally:,} | {murally_ex_mod:,} | {murally_ex_all:,} |\n")
    lines.append(f"| murally share | {pct(total_murally, total_events)} | "
                  f"{pct(murally_ex_mod, total_ex_mod)} | {pct(murally_ex_all, total_ex_all)} |\n")
    lines.append(f"| mural-api share | {pct(mural_api_events, total_events)} | "
                  f"{pct(mural_api_events, total_ex_mod)} | {pct(mural_api_events, total_ex_all)} |\n")
    lines.append(f"| Cross-repo effort events | {effort_events:,} | {effort_events:,} | {effort_events:,} |\n")
    lines.append(f"| Cross-repo effort % | {pct(effort_events, total_events)} | "
                  f"{pct(effort_events, total_ex_mod)} | {pct(effort_events, total_ex_all)} |\n")

    lines.append(f"\nModularization accounted for **{mod_event_count}** queue events "
                  f"({pct(mod_event_count, total_events)} of total, "
                  f"{pct(mod_event_count, total_murally)} of murally).\n")

    lines.append("\n## Modularization PR List\n")
    lines.append("### Core modularization PRs\n")
    lines.append("| PR | Title | Branch | Merged | Files | +/- |\n")
    lines.append("|----|-------|--------|--------|-------|-----|\n")
    for pr in sorted(mod_prs, key=lambda p: p["merged_at"]):
        title_short = pr["title"][:60] + ("..." if len(pr["title"]) > 60 else "")
        branch_short = pr["branch"][:40] + ("..." if len(pr["branch"]) > 40 else "")
        lines.append(
            f"| [#{pr['number']}](https://github.com/tactivos/murally/pull/{pr['number']}) "
            f"| {title_short} | `{branch_short}` | {pr['merged_at'][:10]} "
            f"| {pr['changed_files']} | +{pr['additions']}/-{pr['deletions']} |\n"
        )

    if adjacent_prs:
        lines.append("\n### Modularization-adjacent PRs\n")
        lines.append("| PR | Title | Branch | Merged | Files | +/- |\n")
        lines.append("|----|-------|--------|--------|-------|-----|\n")
        for pr in sorted(adjacent_prs, key=lambda p: p["merged_at"]):
            title_short = pr["title"][:60] + ("..." if len(pr["title"]) > 60 else "")
            branch_short = pr["branch"][:40] + ("..." if len(pr["branch"]) > 40 else "")
            lines.append(
                f"| [#{pr['number']}](https://github.com/tactivos/murally/pull/{pr['number']}) "
                f"| {title_short} | `{branch_short}` | {pr['merged_at'][:10]} "
                f"| {pr['changed_files']} | +{pr['additions']}/-{pr['deletions']} |\n"
            )

    lines.append("\n## Conclusion\n")
    if mod_event_count / total_murally < 0.05:
        lines.append(f"Modularization PRs represent a **small fraction** of murally's queue volume "
                      f"({pct(mod_event_count, total_murally)}). Excluding them does not materially "
                      f"change the merge queue analysis findings — murally remains the dominant source "
                      f"of queue events, and cross-repo effort rates remain similar.\n")
    elif mod_event_count / total_murally < 0.15:
        lines.append(f"Modularization PRs represent a **modest fraction** of murally's queue volume "
                      f"({pct(mod_event_count, total_murally)}). Excluding them slightly reduces "
                      f"murally's share but does not fundamentally change the analysis conclusions.\n")
    else:
        lines.append(f"Modularization PRs represent a **significant fraction** of murally's queue volume "
                      f"({pct(mod_event_count, total_murally)}). This is important context for interpreting "
                      f"the merge queue data — a meaningful portion of murally's queue usage was structural "
                      f"reorganization rather than feature work.\n")

    with open(OUTPUT_DOC, "w") as f:
        f.writelines(lines)
    print(f"\nReport written to: {OUTPUT_DOC}")


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
    print(f"  2. Create {LLM_DECISIONS} with your classifications")
    print(f"  3. Run: python classify_modularization_prs.py report")
    print(f"{'='*70}")


def cmd_report(args):
    # Load PR data
    if not os.path.exists(PR_CACHE):
        print(f"Error: {PR_CACHE} not found. Run 'fetch' first.")
        sys.exit(1)
    with open(PR_CACHE) as f:
        prs = json.load(f)

    # Re-bucket (to set bucket field)
    bucket_prs(prs)

    # Load LLM decisions
    llm_decisions: dict = {}
    if os.path.exists(LLM_DECISIONS):
        with open(LLM_DECISIONS) as f:
            llm_decisions = json.load(f)
        print(f"Loaded {len(llm_decisions)} LLM decisions from {LLM_DECISIONS}")
    else:
        print(f"Warning: {LLM_DECISIONS} not found. All ambiguous PRs will default to not_modularization.")

    generate_report(prs, llm_decisions)


def main():
    parser = argparse.ArgumentParser(description="Classify murally modularization PRs")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    fetch_parser = subparsers.add_parser("fetch", help="Fetch PRs and output ambiguous for review")
    fetch_parser.add_argument("--refresh", action="store_true", help="Force re-fetch PR metadata")

    report_parser = subparsers.add_parser("report", help="Generate final report from LLM decisions")

    args = parser.parse_args()
    if args.command == "fetch":
        cmd_fetch(args)
    elif args.command == "report":
        cmd_report(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
