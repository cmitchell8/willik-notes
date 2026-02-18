#!/usr/bin/env python3
"""
Simulate separate merge queues vs the current single queue.

Loads the timeline (queue_events.csv) and CI estimates (ci_estimates.json),
splits events into per-repo queues, simulates independent FIFO processing
using estimated CI times, and compares wait times.

Cross-repo efforts become separate queue entries in the proposed model.

Usage:
    python simulate_separate_queues.py [--data-dir .] [--output-dir .]

Prerequisites:
    Run analyze_queue_timeline.py first to produce queue_events.csv and
    ci_estimates.json.
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional

try:
    from dateutil import parser as dateutil_parser
except ImportError:
    print("Error: 'python-dateutil' required. Install with: pip install python-dateutil")
    sys.exit(1)


FALLBACK_CI_SECONDS = 15 * 60


def parse_ts(ts_str: str) -> datetime:
    return dateutil_parser.isoparse(ts_str).replace(tzinfo=None)


def percentile(data: list[float], p: float) -> float:
    if not data:
        return 0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * p / 100
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[-1]
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])


def load_ci_estimates(data_dir: str) -> dict[str, float]:
    """Load per-repo CI time estimates (seconds) from ci_estimates.json."""
    filepath = os.path.join(data_dir, "ci_estimates.json")
    if not os.path.exists(filepath):
        print(f"Warning: {filepath} not found, using fallback CI estimates")
        return {}
    with open(filepath) as f:
        return json.load(f)


def load_timeline(data_dir: str) -> list[dict[str, Any]]:
    filepath = os.path.join(data_dir, "queue_events.csv")
    if not os.path.exists(filepath):
        print(f"Error: {filepath} not found. Run analyze_queue_timeline.py first.")
        sys.exit(1)

    events = []
    with open(filepath, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            events.append(
                {
                    "enqueue_time": parse_ts(row["enqueue_time"]),
                    "terminal_time": parse_ts(row["terminal_time"]),
                    "terminal_type": row["terminal_type"],
                    "repo": row["repo"],
                    "pr_number": int(row["pr_number"]),
                    "branch": row["branch"],
                    "queue_wait_s": float(row["queue_wait_s"]),
                    "processing_time_s": float(row["processing_time_s"]),
                    "total_time_s": float(row["total_time_s"]),
                    "is_effort": row["is_effort"] == "True",
                    "effort_size": int(row["effort_size"]),
                    "effort_repos": row["effort_repos"].split(";")
                    if row["effort_repos"]
                    else [],
                }
            )

    print(f"  Loaded {len(events)} events from queue_events.csv")
    return events


def expand_efforts_to_separate_entries(
    events: list[dict[str, Any]],
    ci_estimates: dict[str, float],
) -> list[dict[str, Any]]:
    """For cross-repo efforts, create separate entries for each repo.

    In the proposed model, efforts don't exist — each repo ships independently.
    Processing time per-repo entry uses the CI estimate for that repo.
    """
    expanded = []

    for event in events:
        if not event["is_effort"] or event["effort_size"] <= 1:
            repo = event["repo"]
            if event["terminal_type"] in ("failed", "cancelled"):
                proposed_time = event["total_time_s"]
            else:
                proposed_time = ci_estimates.get(repo, FALLBACK_CI_SECONDS)
            expanded.append(
                {
                    **event,
                    "proposed_repo": repo,
                    "proposed_processing_time_s": proposed_time,
                    "was_effort": False,
                }
            )
            continue

        repos = event["effort_repos"]
        if event["terminal_type"] in ("failed", "cancelled"):
            time_per_repo = event["total_time_s"] / len(repos) if repos else event["total_time_s"]
        else:
            time_per_repo = None

        for repo in repos:
            if time_per_repo is not None:
                proc_time = time_per_repo
            else:
                proc_time = ci_estimates.get(repo, FALLBACK_CI_SECONDS)
            expanded.append(
                {
                    **event,
                    "proposed_repo": repo,
                    "proposed_processing_time_s": proc_time,
                    "was_effort": True,
                }
            )

    expanded.sort(key=lambda e: e["enqueue_time"])
    return expanded


def simulate_separate_queues(
    expanded_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Simulate independent per-repo FIFO queues using estimated CI times."""
    repo_queues: dict[str, list[dict]] = defaultdict(list)

    for event in expanded_events:
        repo_queues[event["proposed_repo"]].append(event)

    results = []

    for repo, repo_events in repo_queues.items():
        repo_events.sort(key=lambda e: e["enqueue_time"])
        prev_end: Optional[datetime] = None

        for event in repo_events:
            enqueue = event["enqueue_time"]
            processing_s = event["proposed_processing_time_s"]

            if prev_end is None:
                new_start = enqueue
            else:
                new_start = max(enqueue, prev_end)

            new_end = new_start + timedelta(seconds=processing_s)
            new_wait_s = (new_start - enqueue).total_seconds()
            new_total_s = (new_end - enqueue).total_seconds()

            results.append(
                {
                    **event,
                    "proposed_processing_start": new_start,
                    "proposed_terminal_time": new_end,
                    "proposed_wait_s": new_wait_s,
                    "proposed_total_s": new_total_s,
                    "time_saved_s": event["queue_wait_s"] - new_wait_s,
                }
            )

            prev_end = new_end

    results.sort(key=lambda e: e["enqueue_time"])
    return results


def compute_comparison(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute comparison metrics between single queue and separate queues."""
    current_waits = [r["queue_wait_s"] for r in results]
    proposed_waits = [r["proposed_wait_s"] for r in results]
    time_saved = [r["time_saved_s"] for r in results]

    current_totals = [r["total_time_s"] for r in results]
    proposed_totals = [r["proposed_total_s"] for r in results]

    repo_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"current_waits": [], "proposed_waits": [], "count": 0}
    )
    for r in results:
        repo = r["proposed_repo"]
        repo_stats[repo]["current_waits"].append(r["queue_wait_s"])
        repo_stats[repo]["proposed_waits"].append(r["proposed_wait_s"])
        repo_stats[repo]["count"] += 1

    effort_entries = sum(1 for r in results if r.get("was_effort", False))
    non_effort_entries = sum(1 for r in results if not r.get("was_effort", False))

    def safe_mean(data: list[float]) -> float:
        return sum(data) / len(data) if data else 0

    def safe_pct_improvement(current: float, proposed: float) -> float:
        if current == 0:
            return 0
        return (current - proposed) / current * 100

    mean_current = safe_mean(current_waits)
    mean_proposed = safe_mean(proposed_waits)
    median_current = percentile(current_waits, 50)
    median_proposed = percentile(proposed_waits, 50)
    p95_current = percentile(current_waits, 95)
    p95_proposed = percentile(proposed_waits, 95)

    return {
        "overview": {
            "total_queue_entries_current": non_effort_entries,
            "total_queue_entries_proposed": len(results),
            "effort_entries_added": effort_entries,
            "date_range_days": round(
                (results[-1]["enqueue_time"] - results[0]["enqueue_time"]).total_seconds()
                / 86400,
                1,
            )
            if results
            else 0,
        },
        "wait_time_comparison": {
            "mean": {
                "current_min": round(mean_current / 60, 1),
                "proposed_min": round(mean_proposed / 60, 1),
                "improvement_min": round((mean_current - mean_proposed) / 60, 1),
                "improvement_pct": round(
                    safe_pct_improvement(mean_current, mean_proposed), 1
                ),
            },
            "median": {
                "current_min": round(median_current / 60, 1),
                "proposed_min": round(median_proposed / 60, 1),
                "improvement_min": round((median_current - median_proposed) / 60, 1),
                "improvement_pct": round(
                    safe_pct_improvement(median_current, median_proposed), 1
                ),
            },
            "p95": {
                "current_min": round(p95_current / 60, 1),
                "proposed_min": round(p95_proposed / 60, 1),
                "improvement_min": round((p95_current - p95_proposed) / 60, 1),
                "improvement_pct": round(
                    safe_pct_improvement(p95_current, p95_proposed), 1
                ),
            },
        },
        "total_time_comparison": {
            "mean": {
                "current_min": round(safe_mean(current_totals) / 60, 1),
                "proposed_min": round(safe_mean(proposed_totals) / 60, 1),
            },
            "median": {
                "current_min": round(percentile(current_totals, 50) / 60, 1),
                "proposed_min": round(percentile(proposed_totals, 50) / 60, 1),
            },
        },
        "time_saved": {
            "mean_min": round(safe_mean(time_saved) / 60, 1),
            "median_min": round(percentile(time_saved, 50) / 60, 1),
            "p95_min": round(percentile(time_saved, 95) / 60, 1),
            "total_hours": round(sum(time_saved) / 3600, 1),
        },
        "repo_breakdown": {
            repo: {
                "count": stats["count"],
                "current_mean_min": round(
                    safe_mean(stats["current_waits"]) / 60, 1
                ),
                "proposed_mean_min": round(
                    safe_mean(stats["proposed_waits"]) / 60, 1
                ),
                "current_median_min": round(
                    percentile(stats["current_waits"], 50) / 60, 1
                ),
                "proposed_median_min": round(
                    percentile(stats["proposed_waits"], 50) / 60, 1
                ),
                "improvement_pct": round(
                    safe_pct_improvement(
                        safe_mean(stats["current_waits"]),
                        safe_mean(stats["proposed_waits"]),
                    ),
                    1,
                ),
            }
            for repo, stats in sorted(
                repo_stats.items(), key=lambda x: -x[1]["count"]
            )
        },
    }


def write_simulation_csv(results: list[dict[str, Any]], output_dir: str) -> str:
    filepath = os.path.join(output_dir, "simulation_results.csv")
    fieldnames = [
        "enqueue_time",
        "repo",
        "proposed_repo",
        "pr_number",
        "branch",
        "terminal_type",
        "current_wait_s",
        "proposed_wait_s",
        "time_saved_s",
        "current_total_s",
        "proposed_total_s",
        "processing_time_s",
        "proposed_processing_time_s",
        "was_effort",
        "effort_size",
    ]

    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow(
                {
                    "enqueue_time": r["enqueue_time"].isoformat(),
                    "repo": r["repo"],
                    "proposed_repo": r["proposed_repo"],
                    "pr_number": r["pr_number"],
                    "branch": r["branch"],
                    "terminal_type": r["terminal_type"],
                    "current_wait_s": round(r["queue_wait_s"], 1),
                    "proposed_wait_s": round(r["proposed_wait_s"], 1),
                    "time_saved_s": round(r["time_saved_s"], 1),
                    "current_total_s": round(r["total_time_s"], 1),
                    "proposed_total_s": round(r["proposed_total_s"], 1),
                    "processing_time_s": round(r["processing_time_s"], 1),
                    "proposed_processing_time_s": round(r["proposed_processing_time_s"], 1),
                    "was_effort": r.get("was_effort", False),
                    "effort_size": r["effort_size"],
                }
            )

    return filepath


def write_weekly_summary(results: list[dict[str, Any]], output_dir: str) -> str:
    filepath = os.path.join(output_dir, "weekly_summary.csv")

    weeks: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"current_waits": [], "proposed_waits": [], "time_saved": []}
    )

    for r in results:
        iso_year, iso_week, _ = r["enqueue_time"].isocalendar()
        week_key = f"{iso_year}-W{iso_week:02d}"
        weeks[week_key]["current_waits"].append(r["queue_wait_s"])
        weeks[week_key]["proposed_waits"].append(r["proposed_wait_s"])
        weeks[week_key]["time_saved"].append(r["time_saved_s"])

    fieldnames = [
        "week",
        "event_count",
        "current_mean_wait_min",
        "proposed_mean_wait_min",
        "improvement_min",
        "improvement_pct",
        "current_p95_wait_min",
        "proposed_p95_wait_min",
        "total_time_saved_hours",
    ]

    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for week in sorted(weeks.keys()):
            data = weeks[week]
            n = len(data["current_waits"])
            curr_mean = sum(data["current_waits"]) / n
            prop_mean = sum(data["proposed_waits"]) / n
            writer.writerow(
                {
                    "week": week,
                    "event_count": n,
                    "current_mean_wait_min": round(curr_mean / 60, 1),
                    "proposed_mean_wait_min": round(prop_mean / 60, 1),
                    "improvement_min": round((curr_mean - prop_mean) / 60, 1),
                    "improvement_pct": round(
                        (curr_mean - prop_mean) / curr_mean * 100 if curr_mean > 0 else 0,
                        1,
                    ),
                    "current_p95_wait_min": round(
                        percentile(data["current_waits"], 95) / 60, 1
                    ),
                    "proposed_p95_wait_min": round(
                        percentile(data["proposed_waits"], 95) / 60, 1
                    ),
                    "total_time_saved_hours": round(
                        sum(data["time_saved"]) / 3600, 1
                    ),
                }
            )

    return filepath


def write_queue_depth_timeseries(
    results: list[dict[str, Any]], output_dir: str
) -> str:
    """Compute hourly queue depth by repo for both models."""
    filepath = os.path.join(output_dir, "queue_depth_timeseries.csv")

    if not results:
        return filepath

    min_time = min(r["enqueue_time"] for r in results)
    max_time = max(
        max(r["terminal_time"] for r in results),
        max(r["proposed_terminal_time"] for r in results),
    )

    min_hour = min_time.replace(minute=0, second=0, microsecond=0)
    max_hour = max_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    fieldnames = ["timestamp", "current_total", "proposed_total"]
    all_repos = sorted(set(r["proposed_repo"] for r in results))
    for repo in all_repos:
        fieldnames.append(f"proposed_{repo}")

    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        current_hour = min_hour
        while current_hour < max_hour:
            row: dict[str, Any] = {"timestamp": current_hour.isoformat()}

            current_depth = sum(
                1
                for r in results
                if not r.get("was_effort", False)
                and r["enqueue_time"] <= current_hour < r["terminal_time"]
            )
            row["current_total"] = current_depth

            proposed_total = 0
            for repo in all_repos:
                repo_depth = sum(
                    1
                    for r in results
                    if r["proposed_repo"] == repo
                    and r["enqueue_time"] <= current_hour < r["proposed_terminal_time"]
                )
                row[f"proposed_{repo}"] = repo_depth
                proposed_total += repo_depth
            row["proposed_total"] = proposed_total

            writer.writerow(row)
            current_hour += timedelta(hours=1)

    return filepath


def write_wait_time_distribution(
    results: list[dict[str, Any]], output_dir: str
) -> str:
    filepath = os.path.join(output_dir, "wait_time_distribution.csv")

    bucket_edges_min = [0, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 360, float("inf")]
    bucket_labels = [
        "0-5",
        "5-10",
        "10-15",
        "15-20",
        "20-30",
        "30-45",
        "45-60",
        "60-90",
        "90-120",
        "120-180",
        "180-240",
        "240-360",
        "360+",
    ]

    current_counts = [0] * len(bucket_labels)
    proposed_counts = [0] * len(bucket_labels)

    for r in results:
        curr_min = r["queue_wait_s"] / 60
        prop_min = r["proposed_wait_s"] / 60

        for i in range(len(bucket_labels)):
            if bucket_edges_min[i] <= curr_min < bucket_edges_min[i + 1]:
                current_counts[i] += 1
                break
        for i in range(len(bucket_labels)):
            if bucket_edges_min[i] <= prop_min < bucket_edges_min[i + 1]:
                proposed_counts[i] += 1
                break

    fieldnames = [
        "wait_time_bucket_min",
        "current_count",
        "proposed_count",
        "current_pct",
        "proposed_pct",
    ]

    total = len(results) or 1

    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i, label in enumerate(bucket_labels):
            writer.writerow(
                {
                    "wait_time_bucket_min": label,
                    "current_count": current_counts[i],
                    "proposed_count": proposed_counts[i],
                    "current_pct": round(current_counts[i] / total * 100, 1),
                    "proposed_pct": round(proposed_counts[i] / total * 100, 1),
                }
            )

    return filepath


def print_comparison(comparison: dict[str, Any]) -> None:
    print(f"\n{'='*60}")
    print("SIMULATION RESULTS: Single Queue vs Separate Queues")
    print("=" * 60)

    ov = comparison["overview"]
    print(f"\nOverview:")
    print(f"  Current queue entries:  {ov['total_queue_entries_current']}")
    print(f"  Proposed queue entries: {ov['total_queue_entries_proposed']} (+{ov['effort_entries_added']} from effort expansion)")
    print(f"  Date range: {ov['date_range_days']} days")

    wt = comparison["wait_time_comparison"]
    print(f"\n--- Queue Wait Time Comparison ---")
    print(f"{'Metric':<10} {'Current':>12} {'Proposed':>12} {'Saved':>12} {'Improvement':>12}")
    print(f"{'-'*10} {'-'*12} {'-'*12} {'-'*12} {'-'*12}")
    for label in ["mean", "median", "p95"]:
        row = wt[label]
        print(
            f"{label:<10} {row['current_min']:>10.1f}m {row['proposed_min']:>10.1f}m "
            f"{row['improvement_min']:>10.1f}m {row['improvement_pct']:>10.1f}%"
        )

    ts = comparison["time_saved"]
    print(f"\n--- Time Saved ---")
    print(f"  Mean per PR:      {ts['mean_min']} min")
    print(f"  Median per PR:    {ts['median_min']} min")
    print(f"  P95 per PR:       {ts['p95_min']} min")
    print(f"  Total saved:      {ts['total_hours']} hours over analysis period")

    print(f"\n--- Per-Repo Breakdown ---")
    print(f"{'Repo':<30} {'Count':>6} {'Curr Mean':>12} {'Prop Mean':>12} {'Improvement':>12}")
    print(f"{'-'*30} {'-'*6} {'-'*12} {'-'*12} {'-'*12}")
    for repo, stats in comparison["repo_breakdown"].items():
        print(
            f"{repo:<30} {stats['count']:>6} {stats['current_mean_min']:>10.1f}m "
            f"{stats['proposed_mean_min']:>10.1f}m {stats['improvement_pct']:>10.1f}%"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Simulate separate merge queues vs single queue"
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=".",
        help="Directory containing queue_events.csv and ci_estimates.json (default: .)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=".",
        help="Output directory for results (default: .)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Merge Queue Separation Simulator")
    print("=" * 60)

    print("\nLoading CI estimates...")
    ci_estimates = load_ci_estimates(args.data_dir)
    if ci_estimates:
        for repo, ci_s in sorted(ci_estimates.items(), key=lambda x: -x[1]):
            print(f"  {repo}: {ci_s/60:.1f} min")
    else:
        print("  Using fallback estimates")

    print("\nLoading timeline data...")
    timeline = load_timeline(args.data_dir)

    print(f"\nExpanding cross-repo efforts into separate entries...")
    expanded = expand_efforts_to_separate_entries(timeline, ci_estimates)
    n_original = len(timeline)
    n_expanded = len(expanded)
    n_added = n_expanded - n_original
    print(f"  {n_original} events -> {n_expanded} events (+{n_added} from effort expansion)")

    print(f"\nSimulating separate queues...")
    results = simulate_separate_queues(expanded)

    print(f"\nComputing comparison metrics...")
    comparison = compute_comparison(results)

    sim_csv = write_simulation_csv(results, args.output_dir)
    print(f"\nSimulation CSV: {sim_csv}")

    weekly_csv = write_weekly_summary(results, args.output_dir)
    print(f"Weekly summary CSV: {weekly_csv}")

    dist_csv = write_wait_time_distribution(results, args.output_dir)
    print(f"Wait time distribution CSV: {dist_csv}")

    print(f"\nComputing queue depth timeseries (this may take a moment)...")
    depth_csv = write_queue_depth_timeseries(results, args.output_dir)
    print(f"Queue depth timeseries CSV: {depth_csv}")

    comparison_path = os.path.join(args.output_dir, "simulation_comparison.json")
    with open(comparison_path, "w") as f:
        json.dump(comparison, f, indent=2)
    print(f"Comparison JSON: {comparison_path}")

    print_comparison(comparison)

    print(f"\n{'='*60}")
    print("Simulation complete!")
    print("=" * 60)
    print(f"\nOutput files:")
    print(f"  simulation_results.csv      — per-PR comparison")
    print(f"  weekly_summary.csv          — weekly aggregated metrics")
    print(f"  wait_time_distribution.csv  — histogram of wait times")
    print(f"  queue_depth_timeseries.csv  — hourly queue depth by repo")
    print(f"  simulation_comparison.json  — full comparison metrics")


if __name__ == "__main__":
    main()
