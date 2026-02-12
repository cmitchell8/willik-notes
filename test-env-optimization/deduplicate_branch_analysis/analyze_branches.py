#!/usr/bin/env python3
"""
Analyze PR branch data to identify cross-repo matches and calculate deduplication.

This script loads cached PR data from both repositories, identifies matching
branch names within a configurable time window, and calculates how many unique
test environments would be created after deduplication.

Usage:
    python analyze_branches.py [--window-days N] [--data-dir DIR]
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any


def load_pr_data(filepath: str) -> list[dict[str, Any]]:
    """Load PR data from a JSON file."""
    if not os.path.exists(filepath):
        print(f"Error: Data file not found: {filepath}")
        print("Run fetch_pr_data.py first to fetch the data.")
        sys.exit(1)

    with open(filepath, "r") as f:
        data = json.load(f)

    return data["pull_requests"]


def parse_date(date_str: str) -> datetime:
    """Parse ISO date string to datetime."""
    return datetime.fromisoformat(date_str.replace("Z", "+00:00")).replace(tzinfo=None)


def get_week_start(dt: datetime) -> str:
    """Get the Monday of the week for a given date."""
    # Find the Monday of the week
    days_since_monday = dt.weekday()
    monday = dt - timedelta(days=days_since_monday)
    return monday.strftime("%Y-%m-%d")


def group_prs_by_branch(prs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group PRs by branch name, keeping all PRs for each branch."""
    branches: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pr in prs:
        branches[pr["branch"]].append(pr)
    return dict(branches)


def get_earliest_pr(prs: list[dict[str, Any]]) -> dict[str, Any]:
    """Get the PR with the earliest creation date from a list."""
    return min(prs, key=lambda pr: parse_date(pr["created_at"]))


def analyze_cross_repo_matches(
    murally_branches: dict[str, list[dict[str, Any]]],
    api_branches: dict[str, list[dict[str, Any]]],
    window_days: int,
) -> tuple[list[dict[str, Any]], int]:
    """
    Find branch names that appear in both repos within the time window.

    Returns:
        - List of matched branch details
        - Count of cross-repo matches
    """
    matches = []
    match_count = 0

    # Find branches that exist in both repos
    common_branches = set(murally_branches.keys()) & set(api_branches.keys())

    for branch in common_branches:
        # Get earliest PR from each repo for this branch
        murally_earliest = get_earliest_pr(murally_branches[branch])
        api_earliest = get_earliest_pr(api_branches[branch])

        murally_date = parse_date(murally_earliest["created_at"])
        api_date = parse_date(api_earliest["created_at"])

        # Calculate the difference in days
        delta = abs((murally_date - api_date).days)

        is_match = delta <= window_days

        match_info = {
            "branch": branch,
            "murally_pr": murally_earliest["number"],
            "murally_date": murally_date.strftime("%Y-%m-%d"),
            "murally_pr_count": len(murally_branches[branch]),
            "api_pr": api_earliest["number"],
            "api_date": api_date.strftime("%Y-%m-%d"),
            "api_pr_count": len(api_branches[branch]),
            "delta_days": delta,
            "is_match": is_match,
        }

        matches.append(match_info)

        if is_match:
            match_count += 1

    return matches, match_count


def calculate_weekly_stats(
    murally_prs: list[dict[str, Any]],
    api_prs: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    window_days: int,
) -> list[dict[str, Any]]:
    """Calculate weekly statistics for the analysis."""
    # Group PRs by week
    murally_by_week: dict[str, list[dict[str, Any]]] = defaultdict(list)
    api_by_week: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for pr in murally_prs:
        week = get_week_start(parse_date(pr["created_at"]))
        murally_by_week[week].append(pr)

    for pr in api_prs:
        week = get_week_start(parse_date(pr["created_at"]))
        api_by_week[week].append(pr)

    # Get all weeks
    all_weeks = sorted(set(murally_by_week.keys()) | set(api_by_week.keys()))

    # Create a lookup for matched branches (branches that matched within window)
    matched_branches = {m["branch"] for m in matches if m["is_match"]}

    # Calculate stats for each week
    weekly_stats = []

    for week in all_weeks:
        murally_week_prs = murally_by_week.get(week, [])
        api_week_prs = api_by_week.get(week, [])

        # Get unique branches for this week
        murally_branches = {pr["branch"] for pr in murally_week_prs}
        api_branches = {pr["branch"] for pr in api_week_prs}

        # Branches that appear in both repos this week AND are within the match window
        cross_repo_this_week = murally_branches & api_branches & matched_branches

        # Calculate deduplicated environments for this week
        # Total unique branches = murally_only + api_only + cross_repo (counted once)
        murally_only = murally_branches - api_branches
        api_only = api_branches - murally_branches
        both_repos = murally_branches & api_branches

        # For branches in both repos, check if they're matches or coincidental
        matched_this_week = both_repos & matched_branches
        not_matched_this_week = both_repos - matched_branches

        # Deduplicated count:
        # - All murally-only branches
        # - All api-only branches
        # - Matched branches (counted once)
        # - Non-matched branches that appear in both (counted twice)
        deduplicated_envs = (
            len(murally_only)
            + len(api_only)
            + len(matched_this_week)
            + len(not_matched_this_week) * 2
        )

        weekly_stats.append({
            "week_start": week,
            "murally_prs": len(murally_week_prs),
            "api_prs": len(api_week_prs),
            "combined_prs": len(murally_week_prs) + len(api_week_prs),
            "murally_branches": len(murally_branches),
            "api_branches": len(api_branches),
            "cross_repo_matches": len(matched_this_week),
            "deduplicated_envs": deduplicated_envs,
        })

    return weekly_stats


def print_summary(
    murally_prs: list[dict[str, Any]],
    api_prs: list[dict[str, Any]],
    murally_branches: dict[str, list[dict[str, Any]]],
    api_branches: dict[str, list[dict[str, Any]]],
    matches: list[dict[str, Any]],
    window_days: int,
) -> None:
    """Print summary statistics."""
    total_murally = len(murally_prs)
    total_api = len(api_prs)
    total_combined = total_murally + total_api

    unique_murally = len(murally_branches)
    unique_api = len(api_branches)

    matched_count = sum(1 for m in matches if m["is_match"])
    not_matched_count = len(matches) - matched_count

    # Calculate deduplicated environment count
    # All unique murally branches + all unique api branches - matched branches
    all_branches_murally = set(murally_branches.keys())
    all_branches_api = set(api_branches.keys())

    murally_only = all_branches_murally - all_branches_api
    api_only = all_branches_api - all_branches_murally
    both_repos = all_branches_murally & all_branches_api

    matched_branches = {m["branch"] for m in matches if m["is_match"]}
    not_matched_in_both = both_repos - matched_branches

    # Deduplicated count:
    # - Branches only in murally: counted once
    # - Branches only in api: counted once
    # - Branches in both that match: counted once
    # - Branches in both that don't match: counted twice (coincidental naming)
    deduplicated_count = (
        len(murally_only)
        + len(api_only)
        + len(matched_branches)
        + len(not_matched_in_both) * 2
    )

    # Without deduplication, we'd count all unique branches from both repos
    # But that's not right either - the "naive" count is PR count
    # Let's use unique branches as the baseline
    naive_branch_count = unique_murally + unique_api

    reduction = naive_branch_count - deduplicated_count
    reduction_pct = (reduction / naive_branch_count * 100) if naive_branch_count > 0 else 0

    print("\n" + "=" * 60)
    print("SUMMARY STATISTICS")
    print("=" * 60)

    print(f"\n{'PR Counts':-^50}")
    print(f"Total PRs (murally):           {total_murally:,}")
    print(f"Total PRs (mural-api):         {total_api:,}")
    print(f"Total PRs (combined):          {total_combined:,}")

    print(f"\n{'Branch Analysis':-^50}")
    print(f"Unique branches (murally):     {unique_murally:,}")
    print(f"Unique branches (mural-api):   {unique_api:,}")
    print(f"Branches in both repos:        {len(both_repos):,}")

    header = f"Cross-Repo Matching (window: {window_days} days)"
    print(f"\n{header:-^50}")
    print(f"Cross-repo matches:            {matched_count:,}")
    print(f"Coincidental naming (>window): {not_matched_count:,}")

    print(f"\n{'Deduplication Results':-^50}")
    print(f"Branches only in murally:      {len(murally_only):,}")
    print(f"Branches only in mural-api:    {len(api_only):,}")
    print(f"Matched cross-repo branches:   {len(matched_branches):,}")
    print(f"Non-matched (counted twice):   {len(not_matched_in_both):,}")
    print(f"")
    print(f"Naive branch count:            {naive_branch_count:,}")
    print(f"Deduplicated env count:        {deduplicated_count:,}")
    print(f"Reduction from dedup:          {reduction:,} ({reduction_pct:.1f}%)")

    print("\n" + "=" * 60)


def save_weekly_csv(
    weekly_stats: list[dict[str, Any]], output_dir: str, window_days: int
) -> str:
    """Save weekly statistics to a CSV file."""
    filepath = os.path.join(output_dir, f"weekly_analysis_{window_days}days.csv")

    fieldnames = [
        "week_start",
        "murally_prs",
        "api_prs",
        "combined_prs",
        "murally_branches",
        "api_branches",
        "cross_repo_matches",
        "deduplicated_envs",
    ]

    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(weekly_stats)

    print(f"Weekly stats saved to: {filepath}")
    return filepath


def save_matched_branches_sample(
    matches: list[dict[str, Any]], output_dir: str, window_days: int, sample_size: int = 20
) -> str:
    """Save a sample of matched branches for validation."""
    filepath = os.path.join(output_dir, f"matched_branches_sample_{window_days}days.txt")

    # Filter to only matches and sort by branch name
    matched = [m for m in matches if m["is_match"]]
    matched.sort(key=lambda m: m["branch"])

    # Also include some non-matches for comparison
    not_matched = [m for m in matches if not m["is_match"]]
    not_matched.sort(key=lambda m: m["delta_days"])

    with open(filepath, "w") as f:
        f.write("=" * 70 + "\n")
        f.write("CROSS-REPO BRANCH MATCHES (within time window)\n")
        f.write("=" * 70 + "\n\n")

        for match in matched[:sample_size]:
            f.write(f"Branch: {match['branch']}\n")
            f.write(f"  - murally PR #{match['murally_pr']}, created {match['murally_date']}")
            f.write(f" ({match['murally_pr_count']} PR(s) total)\n")
            f.write(f"  - mural-api PR #{match['api_pr']}, created {match['api_date']}")
            f.write(f" ({match['api_pr_count']} PR(s) total)\n")
            f.write(f"  - Delta: {match['delta_days']} day(s) (MATCHED)\n")
            f.write("\n")

        if len(matched) > sample_size:
            f.write(f"... and {len(matched) - sample_size} more matched branches\n\n")

        f.write("\n" + "=" * 70 + "\n")
        f.write("COINCIDENTAL NAMING (same branch name, but >window apart)\n")
        f.write("=" * 70 + "\n\n")

        for match in not_matched[:10]:
            f.write(f"Branch: {match['branch']}\n")
            f.write(f"  - murally PR #{match['murally_pr']}, created {match['murally_date']}\n")
            f.write(f"  - mural-api PR #{match['api_pr']}, created {match['api_date']}\n")
            f.write(f"  - Delta: {match['delta_days']} day(s) (NOT MATCHED - coincidental)\n")
            f.write("\n")

        if len(not_matched) > 10:
            f.write(f"... and {len(not_matched) - 10} more coincidental branches\n")

    print(f"Matched branches sample saved to: {filepath}")
    return filepath


def main():
    parser = argparse.ArgumentParser(
        description="Analyze PR branch data for cross-repo deduplication"
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=3,
        help="Time window (days) for matching cross-repo branches, default: 3",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=".",
        help="Directory containing PR data JSON files, default: current directory",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=".",
        help="Output directory for analysis results, default: current directory",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("PR Branch Deduplication Analysis")
    print("=" * 60)
    print(f"Time window for matching: {args.window_days} days")

    # Load PR data
    murally_filepath = os.path.join(args.data_dir, "pr_data_murally.json")
    api_filepath = os.path.join(args.data_dir, "pr_data_mural-api.json")

    print(f"\nLoading data from: {args.data_dir}")
    murally_prs = load_pr_data(murally_filepath)
    api_prs = load_pr_data(api_filepath)

    print(f"  Loaded {len(murally_prs):,} PRs from murally")
    print(f"  Loaded {len(api_prs):,} PRs from mural-api")

    # Group by branch name
    murally_branches = group_prs_by_branch(murally_prs)
    api_branches = group_prs_by_branch(api_prs)

    print(f"\n  Unique branches in murally: {len(murally_branches):,}")
    print(f"  Unique branches in mural-api: {len(api_branches):,}")

    # Find cross-repo matches
    print(f"\nAnalyzing cross-repo matches (window: {args.window_days} days)...")
    matches, match_count = analyze_cross_repo_matches(
        murally_branches, api_branches, args.window_days
    )
    print(f"  Found {len(matches):,} branches in both repos")
    print(f"  Of which {match_count:,} are within the time window")

    # Print summary
    print_summary(
        murally_prs,
        api_prs,
        murally_branches,
        api_branches,
        matches,
        args.window_days,
    )

    # Calculate weekly statistics
    print("\nCalculating weekly statistics...")
    weekly_stats = calculate_weekly_stats(
        murally_prs, api_prs, matches, args.window_days
    )

    # Save outputs
    print("\nSaving output files...")
    save_weekly_csv(weekly_stats, args.output_dir, args.window_days)
    save_matched_branches_sample(matches, args.output_dir, args.window_days)

    print("\n" + "=" * 60)
    print("Analysis complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
