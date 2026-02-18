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
import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional

try:
    from dateutil import parser as dateutil_parser
except ImportError:
    print("Error: 'python-dateutil' required. Install with: pip install python-dateutil")
    sys.exit(1)


FALLBACK_CI_SECONDS = 15 * 60
FRONT_OF_QUEUE_THRESHOLD_S = 60


class ProcessingTimeMode(Enum):
    CONSTANT = "constant"
    OBSERVED = "observed"
    SAMPLED = "sampled"


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


def build_processing_time_distribution(
    events: list[dict[str, Any]],
    ci_estimates: dict[str, float],
    threshold_s: float = FRONT_OF_QUEUE_THRESHOLD_S,
) -> dict[str, list[float]]:
    """Collect observed CI durations from front-of-queue merged events.

    For merged events with queue_wait_s < threshold, total_time_s approximates
    actual CI duration. Returns repo -> list of observed processing times.
    """
    repo_times: dict[str, list[float]] = defaultdict(list)
    for e in events:
        if e["terminal_type"] != "merged":
            continue
        if e["queue_wait_s"] >= threshold_s:
            continue
        if e.get("is_effort") and e["effort_size"] > 1:
            for r in e["effort_repos"]:
                repo_times[r].append(e["total_time_s"])
        else:
            repo_times[e["repo"]].append(e["total_time_s"])
    return dict(repo_times)


def _get_processing_time(
    event: dict[str, Any],
    repo: str,
    mode: ProcessingTimeMode,
    ci_estimates: dict[str, float],
    distributions: dict[str, list[float]] | None,
    rng: random.Random | None,
) -> float:
    ci = ci_estimates.get(repo, FALLBACK_CI_SECONDS)
    if mode == ProcessingTimeMode.CONSTANT:
        return ci
    if mode == ProcessingTimeMode.OBSERVED:
        if (
            event["terminal_type"] == "merged"
            and event["queue_wait_s"] < FRONT_OF_QUEUE_THRESHOLD_S
        ):
            return event["processing_time_s"]
        return ci
    if mode == ProcessingTimeMode.SAMPLED and distributions and repo in distributions:
        times = distributions[repo]
        if len(times) >= 5 and rng is not None:
            return rng.choice(times)
    return ci


def expand_efforts_to_separate_entries(
    events: list[dict[str, Any]],
    ci_estimates: dict[str, float],
    mode: ProcessingTimeMode = ProcessingTimeMode.CONSTANT,
    processing_distributions: dict[str, list[float]] | None = None,
    rng: random.Random | None = None,
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
                proposed_time = _get_processing_time(
                    event, repo, mode, ci_estimates,
                    processing_distributions, rng,
                )
            expanded.append(
                {
                    **event,
                    "proposed_repo": repo,
                    "proposed_processing_time_s": proposed_time,
                    "was_effort": False,
                    "current_wait_attributed_s": event["queue_wait_s"],
                }
            )
            continue

        repos = event["effort_repos"]
        if event["terminal_type"] in ("failed", "cancelled"):
            time_per_repo = event["total_time_s"] / len(repos) if repos else event["total_time_s"]
        else:
            time_per_repo = None

        attributed_wait = event["queue_wait_s"] / event["effort_size"]
        for repo in repos:
            if time_per_repo is not None:
                proc_time = time_per_repo
            else:
                proc_time = _get_processing_time(
                    event, repo, mode, ci_estimates,
                    processing_distributions, rng,
                )
            expanded.append(
                {
                    **event,
                    "proposed_repo": repo,
                    "proposed_processing_time_s": proc_time,
                    "was_effort": True,
                    "current_wait_attributed_s": attributed_wait,
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


def compute_comparison(
    results: list[dict[str, Any]], timeline: list[dict[str, Any]]
) -> dict[str, Any]:
    """Compute comparison metrics between single queue and separate queues.

    Uses the original timeline (before effort expansion) for current-state
    baseline metrics to avoid double-counting effort waits. Proposed metrics
    come from the expanded simulation results.
    """
    current_waits = [e["queue_wait_s"] for e in timeline]
    proposed_waits = [r["proposed_wait_s"] for r in results]
    time_saved = [r["time_saved_s"] for r in results]

    current_totals = [e["total_time_s"] for e in timeline]
    proposed_totals = [r["proposed_total_s"] for r in results]

    current_by_repo: dict[str, list[float]] = defaultdict(list)
    for e in timeline:
        attributed = (
            e["queue_wait_s"] / e["effort_size"]
            if e.get("is_effort") and e.get("effort_size", 1) > 1
            else e["queue_wait_s"]
        )
        repos = (
            e["effort_repos"]
            if e.get("is_effort") and e.get("effort_repos")
            else [e["repo"]]
        )
        for repo in repos:
            current_by_repo[repo].append(attributed)

    proposed_by_repo: dict[str, list[float]] = defaultdict(list)
    for r in results:
        proposed_by_repo[r["proposed_repo"]].append(r["proposed_wait_s"])

    all_repos = sorted(set(current_by_repo) | set(proposed_by_repo))
    repo_stats = {
        repo: {
            "current_waits": current_by_repo.get(repo, []),
            "proposed_waits": proposed_by_repo.get(repo, []),
            "count": len(proposed_by_repo.get(repo, [])),
        }
        for repo in all_repos
    }

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
        "current_wait_attributed_s",
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
                    "current_wait_attributed_s": round(r["current_wait_attributed_s"], 1),
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
        weeks[week_key]["current_waits"].append(r["current_wait_attributed_s"])
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
        curr_min = r["current_wait_attributed_s"] / 60
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


def write_sensitivity_comparison(
    const: dict[str, Any],
    observed: dict[str, Any],
    sampled: dict[str, Any],
    output_dir: str,
) -> str:
    """Write side-by-side comparison of all three processing modes."""
    filepath = os.path.join(output_dir, "sensitivity_comparison.json")

    def _delta(variable: dict, constant: dict) -> dict[str, float]:
        result = {}
        for metric in ("mean", "median", "p95"):
            var_val = variable["wait_time_comparison"][metric]["proposed_min"]
            const_val = constant["wait_time_comparison"][metric]["proposed_min"]
            result[f"{metric}_wait_increase_min"] = round(var_val - const_val, 2)
            const_imp = constant["wait_time_comparison"][metric]["improvement_pct"]
            var_imp = variable["wait_time_comparison"][metric]["improvement_pct"]
            result[f"{metric}_improvement_pct_change"] = round(var_imp - const_imp, 2)
        return result

    output = {
        "constant": const,
        "observed": observed,
        "sampled": sampled,
        "delta_observed_vs_constant": _delta(observed, const),
        "delta_sampled_vs_constant": _delta(sampled, const),
    }
    with open(filepath, "w") as f:
        json.dump(output, f, indent=2)
    return filepath


def print_sensitivity_summary(
    const: dict[str, Any],
    observed: dict[str, Any],
    sampled: dict[str, Any],
) -> None:
    print(f"\n{'='*70}")
    print("SENSITIVITY ANALYSIS: Processing Time Variance")
    print("=" * 70)
    print(
        "\nCompares three processing time modes for the proposed separate queues."
    )
    print("Current-state metrics are identical across modes.\n")

    header = (
        f"{'Metric':<20} {'Constant':>12} {'Observed':>12} {'Sampled':>12} "
        f"{'Current':>12}"
    )
    print(header)
    print("-" * len(header))

    for label in ("mean", "median", "p95"):
        c = const["wait_time_comparison"][label]
        o = observed["wait_time_comparison"][label]
        s = sampled["wait_time_comparison"][label]
        print(
            f"{label + ' wait':<20} "
            f"{c['proposed_min']:>10.1f}m "
            f"{o['proposed_min']:>10.1f}m "
            f"{s['proposed_min']:>10.1f}m "
            f"{c['current_min']:>10.1f}m"
        )

    print()
    for label in ("mean", "median", "p95"):
        c = const["wait_time_comparison"][label]
        o = observed["wait_time_comparison"][label]
        s = sampled["wait_time_comparison"][label]
        print(
            f"{label + ' improvement':<20} "
            f"{c['improvement_pct']:>10.1f}% "
            f"{o['improvement_pct']:>10.1f}% "
            f"{s['improvement_pct']:>10.1f}%"
        )

    print(f"\n--- Distribution Sample Sizes ---")
    # Print from observed/sampled repo breakdown
    for repo in sorted(const["repo_breakdown"].keys()):
        count = const["repo_breakdown"][repo]["count"]
        if count > 50:
            print(f"  {repo}: {count} events")

    print(
        "\nConstant = best-case (P25 CI estimate, deterministic)."
        "\nObserved = uses actual CI time for front-of-queue items."
        "\nSampled = draws from empirical distribution of front-of-queue times."
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
    parser.add_argument(
        "--processing-mode",
        type=str,
        choices=["constant", "observed", "sampled"],
        default="constant",
        help="Processing time mode: constant (CI estimate), observed (timeline when reliable), sampled (empirical dist)",
    )
    parser.add_argument(
        "--run-both",
        action="store_true",
        help="Run all three modes (constant, observed, sampled) and output comparison",
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

    mode = ProcessingTimeMode(args.processing_mode)
    distributions: dict[str, list[float]] | None = None
    rng: random.Random | None = None

    if args.run_both or mode in (ProcessingTimeMode.OBSERVED, ProcessingTimeMode.SAMPLED):
        print(f"\nBuilding empirical processing time distributions...")
        distributions = build_processing_time_distribution(timeline, ci_estimates)
        for repo, times in sorted(distributions.items(), key=lambda x: -len(x[1])):
            if len(times) >= 5:
                mean_t = sum(times) / len(times)
                print(f"  {repo}: {len(times)} samples, mean={mean_t/60:.1f}m, "
                      f"min={min(times)/60:.1f}m, max={max(times)/60:.1f}m")
            else:
                print(f"  {repo}: {len(times)} samples (< 5, will use CI estimate)")

    if mode == ProcessingTimeMode.SAMPLED or args.run_both:
        rng = random.Random(42)

    def _run_mode(run_mode: ProcessingTimeMode, label: str) -> tuple[list[dict], dict]:
        nonlocal rng
        if run_mode == ProcessingTimeMode.SAMPLED:
            rng = random.Random(42)
        print(f"\n--- Running {label} mode ---")
        expanded = expand_efforts_to_separate_entries(
            timeline, ci_estimates, run_mode, distributions, rng,
        )
        n_original = len(timeline)
        n_expanded = len(expanded)
        print(f"  {n_original} events -> {n_expanded} events (+{n_expanded - n_original} from effort expansion)")
        print(f"  Simulating separate queues...")
        results = simulate_separate_queues(expanded)
        print(f"  Computing comparison metrics...")
        comparison = compute_comparison(results, timeline)
        return results, comparison

    if args.run_both:
        results_const, comp_const = _run_mode(ProcessingTimeMode.CONSTANT, "constant")
        results_obs, comp_obs = _run_mode(ProcessingTimeMode.OBSERVED, "observed")
        results_sampled, comp_sampled = _run_mode(ProcessingTimeMode.SAMPLED, "sampled")

        sim_csv = write_simulation_csv(results_const, args.output_dir)
        print(f"\nSimulation CSV (constant): {sim_csv}")
        weekly_csv = write_weekly_summary(results_const, args.output_dir)
        print(f"Weekly summary CSV: {weekly_csv}")
        dist_csv = write_wait_time_distribution(results_const, args.output_dir)
        print(f"Wait time distribution CSV: {dist_csv}")
        print(f"\nComputing queue depth timeseries (this may take a moment)...")
        depth_csv = write_queue_depth_timeseries(results_const, args.output_dir)
        print(f"Queue depth timeseries CSV: {depth_csv}")

        comparison_path = os.path.join(args.output_dir, "simulation_comparison.json")
        with open(comparison_path, "w") as f:
            json.dump(comp_const, f, indent=2)
        print(f"Comparison JSON (constant): {comparison_path}")

        sensitivity_path = write_sensitivity_comparison(
            comp_const, comp_obs, comp_sampled, args.output_dir,
        )
        print(f"Sensitivity comparison JSON: {sensitivity_path}")

        print_comparison(comp_const)
        print_sensitivity_summary(comp_const, comp_obs, comp_sampled)
    else:
        print(f"\nExpanding cross-repo efforts into separate entries ({mode.value} mode)...")
        expanded = expand_efforts_to_separate_entries(
            timeline, ci_estimates, mode, distributions, rng,
        )
        n_original = len(timeline)
        n_expanded = len(expanded)
        n_added = n_expanded - n_original
        print(f"  {n_original} events -> {n_expanded} events (+{n_added} from effort expansion)")

        print(f"\nSimulating separate queues...")
        results = simulate_separate_queues(expanded)

        print(f"\nComputing comparison metrics...")
        comparison = compute_comparison(results, timeline)

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
    if args.run_both:
        print(f"  sensitivity_comparison.json — constant vs observed vs sampled")


if __name__ == "__main__":
    main()
