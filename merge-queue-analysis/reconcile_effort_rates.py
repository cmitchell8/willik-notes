#!/usr/bin/env python3
"""
Reconcile shipit effort detection with branch-name matching.

Cross-references:
- merge-queue-analysis/shipit_data_murally.json, shipit_data_mural-api.json
- test-env-optimization/deduplicate_branch_analysis/pr_data_*.json

Outputs:
- reconciliation_summary.json
- reconciliation_report.md
"""

import json
import os
from collections import defaultdict
from datetime import datetime
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BRANCH_DATA_DIR = os.path.join(
    os.path.dirname(SCRIPT_DIR),
    "test-env-optimization",
    "deduplicate_branch_analysis",
)

WINDOW_DAYS = 3

# Common date range (intersection of both datasets)
COMMON_START = datetime(2025, 2, 18)
COMMON_END = datetime(2026, 2, 3)


def normalize_repo(repo: str) -> str:
    return repo.split("/")[-1] if "/" in repo else repo


def parse_date(date_str: str) -> datetime:
    return datetime.fromisoformat(date_str.replace("Z", "+00:00")).replace(tzinfo=None)


def in_common_range(date_str: str) -> bool:
    dt = parse_date(date_str)
    return COMMON_START <= dt <= COMMON_END


def load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Step 1: Build shipit effort PR pairs (murally + mural-api only)
# ---------------------------------------------------------------------------

def build_effort_pairs(
    shipit_murally: dict, shipit_api: dict
) -> tuple[
    set[frozenset[tuple[str, int]]],
    dict[frozenset[tuple[str, int]], dict],
]:
    """
    Extract unique cross-repo effort PR pairs from shipit data.

    Returns:
        effort_pairs: set of frozenset((repo, pr_num), (repo, pr_num))
        effort_details: map from pair -> {branch, timestamp, primary_repo, primary_pr}
    """
    effort_pairs: set[frozenset[tuple[str, int]]] = set()
    effort_details: dict[frozenset[tuple[str, int]], dict] = {}

    for repo_data in [shipit_murally, shipit_api]:
        for pr in repo_data["pull_requests"]:
            if not in_common_range(pr["created_at"]):
                continue
            for event in pr.get("shipit_events", []):
                if event.get("type") != "enqueued":
                    continue
                effort_prs = event.get("effort_prs", [])
                if len(effort_prs) < 2:
                    continue

                normalized = [
                    (normalize_repo(p["repository"]), p["pr_number"])
                    for p in effort_prs
                    if normalize_repo(p["repository"]) in ("murally", "mural-api")
                ]
                if len(normalized) < 2:
                    continue

                # Keep only the murally+mural-api pair
                murally_prs = [p for p in normalized if p[0] == "murally"]
                api_prs = [p for p in normalized if p[0] == "mural-api"]
                if not murally_prs or not api_prs:
                    continue

                pair = frozenset({murally_prs[0], api_prs[0]})
                if pair not in effort_pairs:
                    effort_pairs.add(pair)
                    effort_details[pair] = {
                        "branch": pr["branch"],
                        "timestamp": event.get("timestamp", ""),
                        "primary_repo": normalize_repo(repo_data["repository"]),
                        "primary_pr": pr["number"],
                    }

    return effort_pairs, effort_details


# ---------------------------------------------------------------------------
# Step 2: Build branch-matched PR pairs
# ---------------------------------------------------------------------------

def group_prs_by_branch(prs: list[dict]) -> dict[str, list[dict]]:
    branches: dict[str, list[dict]] = defaultdict(list)
    for pr in prs:
        branches[pr["branch"]].append(pr)
    return dict(branches)


def get_earliest_pr(prs: list[dict]) -> dict:
    return min(prs, key=lambda p: parse_date(p["created_at"]))


def build_branch_matched_pairs(
    murally_prs: list[dict], api_prs: list[dict], window_days: int
) -> list[dict]:
    """
    Find branches present in both repos within the time window.
    Returns list of dicts with murally_pr, api_pr, branch, delta_days.
    """
    murally_branches = group_prs_by_branch(
        [p for p in murally_prs if in_common_range(p["created_at"])]
    )
    api_branches = group_prs_by_branch(
        [p for p in api_prs if in_common_range(p["created_at"])]
    )

    common = set(murally_branches.keys()) & set(api_branches.keys())
    matched = []

    for branch in common:
        m_earliest = get_earliest_pr(murally_branches[branch])
        a_earliest = get_earliest_pr(api_branches[branch])
        delta = abs((parse_date(m_earliest["created_at"]) - parse_date(a_earliest["created_at"])).days)

        if delta <= window_days:
            matched.append({
                "branch": branch,
                "murally_pr": m_earliest["number"],
                "api_pr": a_earliest["number"],
                "murally_date": m_earliest["created_at"],
                "api_date": a_earliest["created_at"],
                "delta_days": delta,
            })

    return matched


# ---------------------------------------------------------------------------
# Step 3: Cross-reference
# ---------------------------------------------------------------------------

def normalize_effort_pair(pair: frozenset[tuple[str, int]]) -> tuple[int, int]:
    """Return (murally_pr_num, api_pr_num) from a frozenset pair."""
    items = list(pair)
    murally = next((p for p in items if p[0] == "murally"), None)
    api = next((p for p in items if p[0] == "mural-api"), None)
    if murally and api:
        return (murally[1], api[1])
    return (0, 0)


def build_pr_branch_lookup(prs: list[dict]) -> dict[int, str]:
    """Map PR number -> branch name."""
    return {pr["number"]: pr["branch"] for pr in prs}


def cross_reference(
    effort_pairs: set[frozenset[tuple[str, int]]],
    effort_details: dict[frozenset[tuple[str, int]], dict],
    branch_matched: list[dict],
    murally_branch_lookup: dict[int, str],
    api_branch_lookup: dict[int, str],
) -> dict[str, Any]:
    effort_normalized = {normalize_effort_pair(p) for p in effort_pairs}
    branch_matched_set = {(m["murally_pr"], m["api_pr"]) for m in branch_matched}
    branch_matched_by_pr = {(m["murally_pr"], m["api_pr"]): m for m in branch_matched}

    # (a) Branch match but NOT effort
    branch_not_effort = []
    for m in branch_matched:
        key = (m["murally_pr"], m["api_pr"])
        if key not in effort_normalized:
            branch_not_effort.append(m)

    # (b) Effort but NOT branch match
    effort_not_branch = []
    for pair in effort_pairs:
        m_num, a_num = normalize_effort_pair(pair)
        if m_num == 0:
            continue
        if (m_num, a_num) not in branch_matched_set:
            m_branch = murally_branch_lookup.get(m_num, "?")
            a_branch = api_branch_lookup.get(a_num, "?")
            detail = effort_details.get(pair, {})
            effort_not_branch.append({
                "murally_pr": m_num,
                "api_pr": a_num,
                "murally_branch": m_branch,
                "api_branch": a_branch,
                "same_branch": m_branch == a_branch,
                "effort_timestamp": detail.get("timestamp", ""),
                "primary_repo": detail.get("primary_repo", ""),
                "primary_pr": detail.get("primary_pr", 0),
            })

    # (c) Overlap
    overlap = []
    for pair in effort_pairs:
        m_num, a_num = normalize_effort_pair(pair)
        if (m_num, a_num) in branch_matched_set:
            overlap.append({
                "murally_pr": m_num,
                "api_pr": a_num,
                "branch": branch_matched_by_pr[(m_num, a_num)]["branch"],
            })

    return {
        "branch_match_not_effort": branch_not_effort,
        "effort_not_branch_match": effort_not_branch,
        "overlap": overlap,
    }


# ---------------------------------------------------------------------------
# Categorize reasons for effort-not-branch-match
# ---------------------------------------------------------------------------

def categorize_effort_not_branch(items: list[dict]) -> dict[str, list[dict]]:
    categories: dict[str, list[dict]] = {
        "different_branches": [],
        "same_branch_outside_window_or_missing": [],
        "pr_not_in_branch_data": [],
    }
    for item in items:
        if item["murally_branch"] == "?" or item["api_branch"] == "?":
            categories["pr_not_in_branch_data"].append(item)
        elif not item["same_branch"]:
            categories["different_branches"].append(item)
        else:
            categories["same_branch_outside_window_or_missing"].append(item)
    return categories


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_summary_json(results: dict, effort_count: int, branch_count: int, path: str):
    overlap_count = len(results["overlap"])
    bne_count = len(results["branch_match_not_effort"])
    enb_count = len(results["effort_not_branch_match"])
    enb_categories = categorize_effort_not_branch(results["effort_not_branch_match"])

    summary = {
        "shipit_effort_pairs_murally_api": effort_count,
        "branch_matched_pairs": branch_count,
        "overlap": overlap_count,
        "branch_match_not_effort": bne_count,
        "effort_not_branch_match": enb_count,
        "effort_not_branch_match_reasons": {
            k: len(v) for k, v in enb_categories.items()
        },
        "branch_match_rate_of_efforts": (
            f"{overlap_count / effort_count * 100:.1f}%" if effort_count else "N/A"
        ),
        "effort_rate_of_branch_matches": (
            f"{overlap_count / branch_count * 100:.1f}%" if branch_count else "N/A"
        ),
    }
    with open(path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"  Wrote {path}")
    return summary


def write_report_md(
    results: dict, summary: dict, effort_count: int, branch_count: int, path: str
):
    enb_categories = categorize_effort_not_branch(results["effort_not_branch_match"])

    lines = [
        "# Effort Rate Reconciliation Report",
        "",
        "## Overview",
        "",
        "This report cross-references two independent measurements of cross-repo effort rates:",
        "- **Shipit comment parsing**: efforts detected from enqueued comment PR links",
        "- **Branch-name matching**: same branch in murally and mural-api within 3-day window",
        "",
        f"Date range restricted to common overlap: {COMMON_START.date()} to {COMMON_END.date()}",
        "",
        "## Summary Counts",
        "",
        "| Category | Count |",
        "|----------|------:|",
        f"| Shipit effort pairs (murally+mural-api) | {effort_count} |",
        f"| Branch-matched pairs | {branch_count} |",
        f"| **Overlap** (in both) | {summary['overlap']} |",
        f"| Branch match but NOT effort | {summary['branch_match_not_effort']} |",
        f"| Effort but NOT branch match | {summary['effort_not_branch_match']} |",
        "",
        "## Venn Diagram (text)",
        "",
        "```",
        f"  Branch matches only: {summary['branch_match_not_effort']}",
        f"         Overlap:      {summary['overlap']}",
        f"  Efforts only:        {summary['effort_not_branch_match']}",
        "```",
        "",
        "## Why Branch Matches Exceed Efforts",
        "",
        f"Of the {branch_count} branch-matched pairs, only {summary['overlap']} "
        f"({summary['effort_rate_of_branch_matches']}) were actually shipped as efforts. "
        f"The remaining {summary['branch_match_not_effort']} pairs had matching branch "
        "names but were shipped independently (separate shipit invocations) or one/both "
        "PRs were never shipped via the merge queue.",
        "",
        "This is the primary driver of the rate discrepancy: branch-name matching is a "
        "**structural** signal (developer intent to do cross-repo work), while shipit "
        "effort linking is a **behavioral** signal (developer chose to bundle PRs).",
        "",
        "## Why Some Efforts Don't Match by Branch",
        "",
        f"Of the {effort_count} shipit effort pairs, {summary['effort_not_branch_match']} "
        "did not appear in branch-matched pairs. Reasons:",
        "",
        "| Reason | Count |",
        "|--------|------:|",
    ]

    for reason, items in enb_categories.items():
        label = reason.replace("_", " ").capitalize()
        lines.append(f"| {label} | {len(items)} |")

    lines += [
        "",
        "- **Different branches**: The murally and mural-api PRs had different branch names "
        "but were still bundled as an effort (e.g., feature work split across repos with "
        "different naming).",
        "- **Same branch outside window or missing**: Same branch name, but the PRs "
        "fell outside the 3-day creation window used by the branch analysis, or the PR "
        "was not in the branch analysis date range.",
        "- **PR not in branch data**: The PR existed in shipit data but was outside the "
        "branch analysis dataset (different date range or not fetched).",
        "",
    ]

    # Sample: branch matches that weren't efforts
    bne = results["branch_match_not_effort"]
    if bne:
        lines += [
            "## Sample: Branch Matches That Weren't Efforts",
            "",
            "| Branch | murally PR | mural-api PR | Delta (days) |",
            "|--------|-----------|-------------|:------------:|",
        ]
        for item in sorted(bne, key=lambda x: x["branch"])[:20]:
            lines.append(
                f"| `{item['branch']}` | #{item['murally_pr']} | #{item['api_pr']} | {item['delta_days']} |"
            )
        if len(bne) > 20:
            lines.append(f"| ... | *{len(bne) - 20} more* | | |")
        lines.append("")

    # Sample: efforts that weren't branch matches
    enb = results["effort_not_branch_match"]
    if enb:
        lines += [
            "## Sample: Efforts That Weren't Branch Matches",
            "",
            "| murally PR | mural-api PR | murally branch | api branch | Same? |",
            "|-----------|-------------|---------------|-----------|:-----:|",
        ]
        for item in sorted(enb, key=lambda x: x["murally_pr"])[:20]:
            same = "Yes" if item["same_branch"] else "No"
            lines.append(
                f"| #{item['murally_pr']} | #{item['api_pr']} "
                f"| `{item['murally_branch']}` | `{item['api_branch']}` | {same} |"
            )
        if len(enb) > 20:
            lines.append(f"| ... | *{len(enb) - 20} more* | | | |")
        lines.append("")

    lines += [
        "## Impact on Queue Separation Analysis",
        "",
        "The discrepancy does not materially change the queue separation recommendation.",
        "",
        "- The shipit-based analysis found **8.0%** of queue events are cross-repo efforts, "
        "producing a **2.3%** increase in queue entries under the proposed model.",
        f"- Branch matching found {branch_count} pairs, but {summary['branch_match_not_effort']} "
        "of those were shipped independently and would NOT create paired queue entries.",
        f"- Only the {summary['overlap']} truly bundled efforts matter for queue modeling.",
        "- Even doubling the effort count would add ~4-5% more entries — still negligible "
        "compared to the **94.7% wait time reduction** from queue separation.",
        "",
    ]

    with open(path, "w") as f:
        f.write("\n".join(lines))
    print(f"  Wrote {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Effort Rate Reconciliation")
    print("=" * 60)
    print(f"Common date range: {COMMON_START.date()} to {COMMON_END.date()}")
    print(f"Branch match window: {WINDOW_DAYS} days")

    # Load shipit data
    print("\nLoading shipit data...")
    shipit_murally = load_json(os.path.join(SCRIPT_DIR, "shipit_data_murally.json"))
    shipit_api = load_json(os.path.join(SCRIPT_DIR, "shipit_data_mural-api.json"))
    print(f"  murally PRs: {len(shipit_murally['pull_requests'])}")
    print(f"  mural-api PRs: {len(shipit_api['pull_requests'])}")

    # Load branch analysis data
    print("\nLoading branch analysis PR data...")
    branch_murally = load_json(os.path.join(BRANCH_DATA_DIR, "pr_data_murally.json"))
    branch_api = load_json(os.path.join(BRANCH_DATA_DIR, "pr_data_mural-api.json"))
    print(f"  murally PRs: {len(branch_murally['pull_requests'])}")
    print(f"  mural-api PRs: {len(branch_api['pull_requests'])}")

    # Step 1: Build effort pairs
    print("\nStep 1: Building shipit effort pairs (murally+mural-api)...")
    effort_pairs, effort_details = build_effort_pairs(shipit_murally, shipit_api)
    print(f"  Found {len(effort_pairs)} unique cross-repo effort pairs")

    # Step 2: Build branch-matched pairs
    print("\nStep 2: Building branch-matched pairs (window={WINDOW_DAYS}d)...")
    branch_matched = build_branch_matched_pairs(
        branch_murally["pull_requests"],
        branch_api["pull_requests"],
        WINDOW_DAYS,
    )
    print(f"  Found {len(branch_matched)} branch-matched pairs")

    # Step 3: Cross-reference
    print("\nStep 3: Cross-referencing...")
    murally_branch_lookup = build_pr_branch_lookup(shipit_murally["pull_requests"])
    api_branch_lookup = build_pr_branch_lookup(shipit_api["pull_requests"])
    # Also add branch data PRs to lookups for fuller coverage
    for pr in branch_murally["pull_requests"]:
        murally_branch_lookup.setdefault(pr["number"], pr["branch"])
    for pr in branch_api["pull_requests"]:
        api_branch_lookup.setdefault(pr["number"], pr["branch"])

    results = cross_reference(
        effort_pairs, effort_details, branch_matched,
        murally_branch_lookup, api_branch_lookup,
    )

    overlap = len(results["overlap"])
    bne = len(results["branch_match_not_effort"])
    enb = len(results["effort_not_branch_match"])
    print(f"  Overlap (in both):          {overlap}")
    print(f"  Branch match, NOT effort:   {bne}")
    print(f"  Effort, NOT branch match:   {enb}")

    # Outputs
    print("\nWriting outputs...")
    summary = write_summary_json(
        results, len(effort_pairs), len(branch_matched),
        os.path.join(SCRIPT_DIR, "reconciliation_summary.json"),
    )
    write_report_md(
        results, summary, len(effort_pairs), len(branch_matched),
        os.path.join(SCRIPT_DIR, "reconciliation_report.md"),
    )

    print("\n" + "=" * 60)
    print("Reconciliation complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
