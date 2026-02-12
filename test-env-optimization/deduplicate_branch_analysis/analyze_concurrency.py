#!/usr/bin/env python3
"""
Analyze concurrent test environment counts based on PR lifespan.

This script calculates how many test environments would be active at any given
time point, accounting for PR close times and cross-repo branch deduplication.

Unlike analyze_branches.py which counts total environments created per week,
this script tracks concurrent environments over time to understand peak
infrastructure load.

Supports TTL capping to model the inactivity TTL that auto-deletes environments.

Usage:
    python analyze_concurrency.py [--resolution hourly|daily] [--data-dir DIR] [--ttl-days N]
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# Lifespan bucket boundaries in hours
LIFESPAN_BUCKETS = [
    (0, 1, "0-1h"),
    (1, 2, "1-2h"),
    (2, 4, "2-4h"),
    (4, 8, "4-8h"),
    (8, 24, "8-24h"),
    (24, 48, "24-48h"),
    (48, 168, "48h-1w"),
    (168, float("inf"), "1w+"),
]

# Threshold for flagging long-lived PRs (in days)
LONG_LIVED_THRESHOLD_DAYS = 30


def load_pr_data(filepath: str) -> list[dict[str, Any]]:
    """Load PR data from a JSON file."""
    if not os.path.exists(filepath):
        print(f"Error: Data file not found: {filepath}")
        print("Run fetch_pr_data.py first to fetch the data.")
        sys.exit(1)

    with open(filepath, "r") as f:
        data = json.load(f)

    return data["pull_requests"]


def parse_datetime(date_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO date string to datetime, handling None."""
    if date_str is None:
        return None
    return datetime.fromisoformat(date_str.replace("Z", "+00:00")).replace(tzinfo=None)


def get_pr_close_time(pr: dict[str, Any]) -> Optional[datetime]:
    """Get the actual close time for a PR (merged_at or closed_at)."""
    # Prefer merged_at if available, otherwise use closed_at
    merged_at = parse_datetime(pr.get("merged_at"))
    if merged_at:
        return merged_at
    return parse_datetime(pr.get("closed_at"))


def get_effective_close_time(
    pr: dict[str, Any],
    analysis_end: datetime,
    ttl_days: Optional[int] = None,
) -> datetime:
    """
    Get effective close time, accounting for TTL cap.

    The effective close time is the minimum of:
    - Actual close time (merged_at or closed_at)
    - TTL expiry time (created_at + ttl_days), if ttl_days is specified
    - Analysis end date, if PR is still open and no TTL is specified

    Args:
        pr: PR data dictionary
        analysis_end: End date for analysis (used for still-open PRs)
        ttl_days: Optional TTL in days to cap environment lifespan

    Returns:
        Effective close datetime
    """
    created_at = parse_datetime(pr["created_at"])
    actual_close = get_pr_close_time(pr)

    # Start with actual close or analysis_end if PR is still open
    effective_close = actual_close if actual_close else analysis_end

    # Apply TTL cap if specified
    if ttl_days is not None and created_at is not None:
        ttl_expiry = created_at + timedelta(days=ttl_days)
        effective_close = min(effective_close, ttl_expiry)

    return effective_close


def is_pr_active_at(
    pr: dict[str, Any],
    time_point: datetime,
    analysis_end: datetime,
    ttl_days: Optional[int] = None,
) -> bool:
    """Check if a PR's environment is active at a specific time point."""
    created_at = parse_datetime(pr["created_at"])
    if created_at is None or created_at > time_point:
        return False

    effective_close = get_effective_close_time(pr, analysis_end, ttl_days)

    # PR is active if: created <= time_point < effective_close
    return created_at <= time_point < effective_close


def get_active_branches(
    prs: list[dict[str, Any]],
    time_point: datetime,
    analysis_end: datetime,
    ttl_days: Optional[int] = None,
) -> set[str]:
    """Get set of branch names with active PRs at a specific time point."""
    active_branches = set()
    for pr in prs:
        if is_pr_active_at(pr, time_point, analysis_end, ttl_days):
            active_branches.add(pr["branch"])
    return active_branches


def generate_time_points(
    start: datetime, end: datetime, resolution: str
) -> list[datetime]:
    """Generate list of time points for the analysis."""
    time_points = []
    current = start

    if resolution == "hourly":
        delta = timedelta(hours=1)
    elif resolution == "daily":
        delta = timedelta(days=1)
    else:
        raise ValueError(f"Unknown resolution: {resolution}")

    while current <= end:
        time_points.append(current)
        current += delta

    return time_points


def generate_time_series(
    murally_prs: list[dict[str, Any]],
    api_prs: list[dict[str, Any]],
    start: datetime,
    end: datetime,
    resolution: str,
    ttl_days: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Generate time series of concurrent environments."""
    time_points = generate_time_points(start, end, resolution)
    results = []

    print(f"Generating time series with {len(time_points):,} time points...")

    for i, t in enumerate(time_points):
        if i % 500 == 0:
            progress = (i / len(time_points)) * 100
            print(f"  Progress: {progress:.1f}% ({i:,}/{len(time_points):,})")

        # Get active branches at this time point
        murally_active = get_active_branches(murally_prs, t, end, ttl_days)
        api_active = get_active_branches(api_prs, t, end, ttl_days)

        # Deduplicate cross-repo branches (union of branch names)
        all_branches = murally_active | api_active

        # Track cross-repo overlap
        cross_repo = murally_active & api_active

        results.append({
            "timestamp": t.isoformat(),
            "murally_active": len(murally_active),
            "api_active": len(api_active),
            "naive_total": len(murally_active) + len(api_active),
            "deduplicated_envs": len(all_branches),
            "cross_repo_active": len(cross_repo),
        })

    print(f"  Progress: 100.0% ({len(time_points):,}/{len(time_points):,})")
    return results


def calculate_pr_lifespan(
    pr: dict[str, Any],
    analysis_end: datetime,
    ttl_days: Optional[int] = None,
) -> Optional[float]:
    """
    Calculate effective PR/environment lifespan in hours.

    Returns None if PR has no created_at. If ttl_days is specified,
    the lifespan is capped at ttl_days * 24 hours.
    """
    created_at = parse_datetime(pr["created_at"])
    if created_at is None:
        return None

    effective_close = get_effective_close_time(pr, analysis_end, ttl_days)
    lifespan = effective_close - created_at
    return lifespan.total_seconds() / 3600  # Convert to hours


def calculate_pr_lifespans(
    prs: list[dict[str, Any]],
    analysis_end: datetime,
    ttl_days: Optional[int] = None,
) -> list[float]:
    """Calculate effective lifespans for all PRs, filtering out None values."""
    lifespans = []
    for pr in prs:
        lifespan = calculate_pr_lifespan(pr, analysis_end, ttl_days)
        if lifespan is not None:
            lifespans.append(lifespan)
    return lifespans


def bucket_lifespans(lifespans: list[float]) -> dict[str, int]:
    """Bucket lifespans into predefined ranges."""
    buckets = {label: 0 for _, _, label in LIFESPAN_BUCKETS}

    for lifespan in lifespans:
        for min_h, max_h, label in LIFESPAN_BUCKETS:
            if min_h <= lifespan < max_h:
                buckets[label] += 1
                break

    return buckets


def percentile(data: list[float], p: float) -> float:
    """Calculate percentile of a sorted list."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (p / 100)
    f = int(k)
    c = f + 1 if f + 1 < len(sorted_data) else f
    return sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f)


def compute_summary_stats(time_series: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute summary statistics from time series data."""
    if not time_series:
        return {}

    deduplicated_envs = [ts["deduplicated_envs"] for ts in time_series]
    naive_totals = [ts["naive_total"] for ts in time_series]
    cross_repo_active = [ts["cross_repo_active"] for ts in time_series]

    # Find peak
    peak_idx = deduplicated_envs.index(max(deduplicated_envs))
    peak_entry = time_series[peak_idx]

    return {
        "peak_concurrent": max(deduplicated_envs),
        "peak_naive": max(naive_totals),
        "peak_timestamp": peak_entry["timestamp"],
        "average_concurrent": sum(deduplicated_envs) / len(deduplicated_envs),
        "average_naive": sum(naive_totals) / len(naive_totals),
        "p95_concurrent": percentile(deduplicated_envs, 95),
        "p50_concurrent": percentile(deduplicated_envs, 50),
        "min_concurrent": min(deduplicated_envs),
        "max_cross_repo": max(cross_repo_active),
        "avg_cross_repo": sum(cross_repo_active) / len(cross_repo_active),
    }


def count_long_lived_prs(prs: list[dict[str, Any]], analysis_end: datetime) -> list[dict[str, Any]]:
    """Find PRs that have been open longer than the threshold."""
    long_lived = []
    threshold_hours = LONG_LIVED_THRESHOLD_DAYS * 24

    for pr in prs:
        lifespan = calculate_pr_lifespan(pr, analysis_end)
        if lifespan is not None and lifespan > threshold_hours:
            long_lived.append({
                "number": pr["number"],
                "branch": pr["branch"],
                "lifespan_days": lifespan / 24,
                "state": pr["state"],
            })

    return long_lived


def get_output_suffix(ttl_days: Optional[int]) -> str:
    """Get filename suffix based on TTL setting."""
    return f"_ttl{ttl_days}" if ttl_days else ""


def save_time_series_csv(
    time_series: list[dict[str, Any]],
    output_dir: str,
    ttl_days: Optional[int] = None,
) -> str:
    """Save time series data to CSV."""
    suffix = get_output_suffix(ttl_days)
    filepath = os.path.join(output_dir, f"concurrent_envs_timeseries{suffix}.csv")

    fieldnames = [
        "timestamp",
        "murally_active",
        "api_active",
        "naive_total",
        "deduplicated_envs",
        "cross_repo_active",
    ]

    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(time_series)

    print(f"Time series saved to: {filepath}")
    return filepath


def save_lifespan_distribution_csv(
    murally_lifespans: list[float],
    api_lifespans: list[float],
    output_dir: str,
    ttl_days: Optional[int] = None,
) -> str:
    """Save lifespan distribution to CSV."""
    suffix = get_output_suffix(ttl_days)
    filepath = os.path.join(output_dir, f"pr_lifespan_distribution{suffix}.csv")

    murally_buckets = bucket_lifespans(murally_lifespans)
    api_buckets = bucket_lifespans(api_lifespans)

    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["lifespan_bucket", "murally_count", "api_count"])

        for _, _, label in LIFESPAN_BUCKETS:
            writer.writerow([label, murally_buckets[label], api_buckets[label]])

    print(f"Lifespan distribution saved to: {filepath}")
    return filepath


def save_summary_txt(
    stats: dict[str, Any],
    murally_lifespans: list[float],
    api_lifespans: list[float],
    murally_long_lived: list[dict[str, Any]],
    api_long_lived: list[dict[str, Any]],
    output_dir: str,
    ttl_days: Optional[int] = None,
) -> str:
    """Save summary statistics to text file."""
    suffix = get_output_suffix(ttl_days)
    filepath = os.path.join(output_dir, f"concurrent_envs_summary{suffix}.txt")

    with open(filepath, "w") as f:
        f.write("=" * 60 + "\n")
        f.write("CONCURRENT ENVIRONMENT ANALYSIS\n")
        f.write("=" * 60 + "\n\n")

        # TTL Cap Information
        if ttl_days is not None:
            f.write(f"TTL Cap Applied: {ttl_days} days ({ttl_days * 24} hours)\n")
            f.write("(Environment lifespans capped at TTL, modeling inactivity expiry)\n\n")
        else:
            f.write("TTL Cap Applied: None (using actual PR close times)\n\n")

        # PR Lifespan Statistics
        lifespan_label = "effective, with TTL cap" if ttl_days else "actual"
        f.write(f"PR Lifespan Statistics ({lifespan_label}):\n")
        f.write("-" * 30 + "\n")

        if murally_lifespans:
            f.write(f"Murally PRs analyzed:          {len(murally_lifespans):,}\n")
            f.write(f"  Median lifespan:             {percentile(murally_lifespans, 50):.1f} hours\n")
            f.write(f"  90th percentile:             {percentile(murally_lifespans, 90):.1f} hours\n")
            f.write(f"  95th percentile:             {percentile(murally_lifespans, 95):.1f} hours\n")
            f.write(f"  Max lifespan:                {max(murally_lifespans):.1f} hours ({max(murally_lifespans)/24:.1f} days)\n")

        f.write("\n")

        if api_lifespans:
            f.write(f"mural-api PRs analyzed:        {len(api_lifespans):,}\n")
            f.write(f"  Median lifespan:             {percentile(api_lifespans, 50):.1f} hours\n")
            f.write(f"  90th percentile:             {percentile(api_lifespans, 90):.1f} hours\n")
            f.write(f"  95th percentile:             {percentile(api_lifespans, 95):.1f} hours\n")
            f.write(f"  Max lifespan:                {max(api_lifespans):.1f} hours ({max(api_lifespans)/24:.1f} days)\n")

        f.write("\n")

        # Concurrent Environment Statistics
        f.write("Concurrent Environment Statistics:\n")
        f.write("-" * 30 + "\n")
        f.write(f"Peak concurrent (naive):       {stats['peak_naive']}\n")
        f.write(f"Peak concurrent (deduplicated):{stats['peak_concurrent']}\n")
        f.write(f"Average concurrent:            {stats['average_concurrent']:.1f}\n")
        f.write(f"Median concurrent (P50):       {stats['p50_concurrent']:.1f}\n")
        f.write(f"P95 concurrent:                {stats['p95_concurrent']:.1f}\n")
        f.write(f"Min concurrent:                {stats['min_concurrent']}\n")
        f.write(f"\nPeak occurred at:              {stats['peak_timestamp']}\n")

        f.write("\n")

        # Cross-Repo Concurrency
        f.write("Cross-Repo Concurrency:\n")
        f.write("-" * 30 + "\n")
        f.write(f"Max branches active in both:   {stats['max_cross_repo']}\n")
        f.write(f"Avg branches active in both:   {stats['avg_cross_repo']:.1f}\n")

        # Deduplication impact
        if stats['average_naive'] > 0:
            dedup_savings_pct = ((stats['average_naive'] - stats['average_concurrent']) / stats['average_naive']) * 100
            f.write(f"Deduplication savings:         {dedup_savings_pct:.1f}% reduction\n")

        f.write("\n")

        # Long-lived PRs
        f.write(f"Long-lived PRs (>{LONG_LIVED_THRESHOLD_DAYS} days):\n")
        f.write("-" * 30 + "\n")
        f.write(f"Murally long-lived PRs:        {len(murally_long_lived)}\n")
        f.write(f"mural-api long-lived PRs:      {len(api_long_lived)}\n")

        if murally_long_lived or api_long_lived:
            f.write("\nTop 5 longest-lived PRs:\n")
            all_long_lived = [
                {**pr, "repo": "murally"} for pr in murally_long_lived
            ] + [
                {**pr, "repo": "mural-api"} for pr in api_long_lived
            ]
            all_long_lived.sort(key=lambda x: x["lifespan_days"], reverse=True)

            for pr in all_long_lived[:5]:
                f.write(f"  - {pr['repo']} #{pr['number']}: {pr['lifespan_days']:.1f} days ({pr['state']})\n")
                f.write(f"    Branch: {pr['branch'][:50]}{'...' if len(pr['branch']) > 50 else ''}\n")

        f.write("\n" + "=" * 60 + "\n")

    print(f"Summary saved to: {filepath}")
    return filepath


def main():
    parser = argparse.ArgumentParser(
        description="Analyze concurrent test environment counts based on PR lifespan"
    )
    parser.add_argument(
        "--resolution",
        choices=["hourly", "daily"],
        default="hourly",
        help="Time resolution for analysis, default: hourly",
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
    parser.add_argument(
        "--ttl-days",
        type=int,
        default=None,
        help="Cap environment lifespan at N days (models inactivity TTL). Default: no cap",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("Concurrent Test Environment Analysis")
    print("=" * 60)
    print(f"Resolution: {args.resolution}")
    if args.ttl_days is not None:
        print(f"TTL Cap: {args.ttl_days} days ({args.ttl_days * 24} hours)")
    else:
        print("TTL Cap: None (using actual PR close times)")

    # Load PR data
    murally_filepath = os.path.join(args.data_dir, "pr_data_murally.json")
    api_filepath = os.path.join(args.data_dir, "pr_data_mural-api.json")

    print(f"\nLoading data from: {args.data_dir}")
    murally_prs = load_pr_data(murally_filepath)
    api_prs = load_pr_data(api_filepath)

    print(f"  Loaded {len(murally_prs):,} PRs from murally")
    print(f"  Loaded {len(api_prs):,} PRs from mural-api")

    # Check if data has close times
    has_close_times = any(pr.get("closed_at") is not None for pr in murally_prs[:100])
    if not has_close_times:
        print("\n" + "!" * 60)
        print("WARNING: PR data does not include close times!")
        print("Re-fetch data with: python fetch_pr_data.py --force")
        print("!" * 60)
        print("\nContinuing with current data (still-open PRs will be treated as open)...\n")

    # Determine analysis time range from data
    all_prs = murally_prs + api_prs
    all_dates = [parse_datetime(pr["created_at"]) for pr in all_prs if pr.get("created_at")]
    all_dates = [d for d in all_dates if d is not None]

    if not all_dates:
        print("Error: No valid PR dates found in data")
        sys.exit(1)

    start_date = min(all_dates)
    end_date = max(all_dates)

    # Extend end_date to account for PRs that might still be open
    # Use UTC to match the timestamp format in the input data (ISO 8601 with Z suffix)
    analysis_end = datetime.now(timezone.utc).replace(tzinfo=None)

    print(f"\nAnalysis period:")
    print(f"  First PR: {start_date}")
    print(f"  Last PR:  {end_date}")
    print(f"  Analysis end: {analysis_end}")

    # Generate time series
    print("\nGenerating time series...")
    time_series = generate_time_series(
        murally_prs, api_prs, start_date, end_date, args.resolution, args.ttl_days
    )

    # Calculate PR lifespans (effective lifespans, accounting for TTL cap)
    print("\nCalculating PR lifespans...")
    murally_lifespans = calculate_pr_lifespans(murally_prs, analysis_end, args.ttl_days)
    api_lifespans = calculate_pr_lifespans(api_prs, analysis_end, args.ttl_days)

    print(f"  Murally: {len(murally_lifespans):,} PRs with valid lifespans")
    print(f"  mural-api: {len(api_lifespans):,} PRs with valid lifespans")

    # Find long-lived PRs (based on actual lifespan, not TTL-capped)
    # This shows which PRs would have been capped by TTL
    murally_long_lived = count_long_lived_prs(murally_prs, analysis_end)
    api_long_lived = count_long_lived_prs(api_prs, analysis_end)

    # Compute summary statistics
    print("\nComputing statistics...")
    stats = compute_summary_stats(time_series)

    # Print key findings
    print("\n" + "=" * 60)
    print("KEY FINDINGS")
    if args.ttl_days is not None:
        print(f"(with {args.ttl_days}-day TTL cap)")
    print("=" * 60)
    print(f"Peak concurrent environments:  {stats['peak_concurrent']}")
    print(f"Average concurrent:            {stats['average_concurrent']:.1f}")
    print(f"P95 concurrent:                {stats['p95_concurrent']:.1f}")
    print(f"Peak occurred at:              {stats['peak_timestamp']}")

    lifespan_label = "effective" if args.ttl_days else "actual"
    if murally_lifespans:
        print(f"\nMedian PR lifespan (murally, {lifespan_label}):  {percentile(murally_lifespans, 50):.1f} hours")
    if api_lifespans:
        print(f"Median PR lifespan (api, {lifespan_label}):      {percentile(api_lifespans, 50):.1f} hours")

    # Show max lifespan when TTL is applied (should be capped)
    if args.ttl_days is not None:
        all_lifespans = murally_lifespans + api_lifespans
        if all_lifespans:
            max_lifespan = max(all_lifespans)
            ttl_hours = args.ttl_days * 24
            print(f"Max effective lifespan:        {max_lifespan:.1f} hours (TTL cap: {ttl_hours} hours)")

    # Save outputs
    print("\nSaving output files...")
    save_time_series_csv(time_series, args.output_dir, args.ttl_days)
    save_lifespan_distribution_csv(murally_lifespans, api_lifespans, args.output_dir, args.ttl_days)
    save_summary_txt(
        stats,
        murally_lifespans,
        api_lifespans,
        murally_long_lived,
        api_long_lived,
        args.output_dir,
        args.ttl_days,
    )

    print("\n" + "=" * 60)
    print("Analysis complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
