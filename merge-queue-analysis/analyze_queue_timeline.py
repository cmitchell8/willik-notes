#!/usr/bin/env python3
"""
Reconstruct the merge queue timeline from shipit events.

Loads cached repo data, extracts queue events (enqueue/merge pairs),
estimates per-repo CI processing time from the data, and derives
queue wait times as total_shipit_time - estimated_ci_time.

Cross-repo efforts are grouped as single queue items.

Usage:
    python analyze_queue_timeline.py [--data-dir .] [--output-dir .]

Prerequisites:
    Run fetch_shipit_data.py first to produce shipit_data_*.json files.
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import Any, Optional

try:
    from dateutil import parser as dateutil_parser
except ImportError:
    print("Error: 'python-dateutil' package is required. Install with: pip install python-dateutil")
    sys.exit(1)


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


def load_repo_data(data_dir: str) -> list[dict[str, Any]]:
    """Load all shipit_data_*.json files from the data directory."""
    all_data = []
    for filename in sorted(os.listdir(data_dir)):
        if filename.startswith("shipit_data_") and filename.endswith(".json"):
            filepath = os.path.join(data_dir, filename)
            with open(filepath) as f:
                data = json.load(f)
            all_data.append(data)
            print(f"  Loaded {filename}: {len(data['pull_requests'])} PRs")
    return all_data


def extract_queue_events(all_repo_data: list[dict]) -> list[dict[str, Any]]:
    """Extract queue events (enqueue -> merge/fail/cancel) from all repos.

    Groups cross-repo efforts as single queue items using the effort PR
    links in enqueued comments.
    """
    effort_groups: dict[str, dict[str, Any]] = {}
    standalone_events: list[dict[str, Any]] = []

    for repo_data in all_repo_data:
        repo = repo_data["repository"]

        for pr in repo_data["pull_requests"]:
            events = pr.get("shipit_events", [])
            if not events:
                continue

            enqueue_events = [e for e in events if e["type"] == "enqueued"]
            terminal_events = [
                e for e in events if e["type"] in ("merged", "failed", "cancelled")
            ]

            for i, enqueue in enumerate(enqueue_events):
                terminal = terminal_events[i] if i < len(terminal_events) else None
                if not terminal:
                    if terminal_events:
                        terminal = terminal_events[-1]
                    elif pr.get("merged_at"):
                        terminal = {
                            "type": "merged",
                            "timestamp": pr["merged_at"],
                        }
                    else:
                        continue

                enqueue_time = parse_ts(enqueue["timestamp"])
                terminal_time = parse_ts(terminal["timestamp"])
                effort_prs = enqueue.get("effort_prs", [])

                if len(effort_prs) > 1:
                    effort_key = _effort_key(effort_prs)
                    if effort_key not in effort_groups:
                        effort_groups[effort_key] = {
                            "enqueue_time": enqueue_time,
                            "terminal_time": terminal_time,
                            "terminal_type": terminal["type"],
                            "repos": set(),
                            "prs": [],
                            "effort_prs": effort_prs,
                            "branch": pr["branch"],
                        }
                    group = effort_groups[effort_key]
                    group["repos"].add(repo)
                    group["prs"].append(
                        {"repo": repo, "number": pr["number"]}
                    )
                    if terminal_time > group["terminal_time"]:
                        group["terminal_time"] = terminal_time
                        group["terminal_type"] = terminal["type"]
                else:
                    standalone_events.append(
                        {
                            "enqueue_time": enqueue_time,
                            "terminal_time": terminal_time,
                            "terminal_type": terminal["type"],
                            "repo": repo,
                            "pr_number": pr["number"],
                            "branch": pr["branch"],
                            "is_effort": False,
                            "effort_size": 1,
                            "effort_repos": [repo],
                        }
                    )

    queue_events = list(standalone_events)
    for key, group in effort_groups.items():
        primary_repo = sorted(group["repos"])[0]
        primary_pr = next(
            (p for p in group["prs"] if p["repo"] == primary_repo),
            group["prs"][0],
        )
        queue_events.append(
            {
                "enqueue_time": group["enqueue_time"],
                "terminal_time": group["terminal_time"],
                "terminal_type": group["terminal_type"],
                "repo": primary_repo,
                "pr_number": primary_pr["number"],
                "branch": group["branch"],
                "is_effort": True,
                "effort_size": len(group["repos"]),
                "effort_repos": sorted(group["repos"]),
            }
        )

    queue_events.sort(key=lambda e: e["enqueue_time"])
    return queue_events


def _effort_key(effort_prs: list[dict]) -> str:
    """Create a unique key for an effort based on its constituent PRs."""
    parts = sorted(f"{p['repository']}#{p['pr_number']}" for p in effort_prs)
    return "|".join(parts)


def estimate_ci_times(
    queue_events: list[dict[str, Any]],
) -> dict[str, float]:
    """Estimate CI processing time per repo from observed data.

    Strategy: For each repo, collect total_shipit_time (enqueue to terminal)
    for MERGED events only. Failed/cancelled items resolve in seconds and
    would pull estimates down unrealistically. Among merged items, the ones
    with the shortest total times likely had no queue wait, so their total
    time approximates CI time. Use the 25th percentile of merged events as
    the estimated CI processing time per repo.

    For efforts, attribute to the primary repo.
    """
    repo_merged_times: dict[str, list[float]] = defaultdict(list)
    repo_all_times: dict[str, list[float]] = defaultdict(list)
    terminal_type_counts: dict[str, int] = defaultdict(int)

    for event in queue_events:
        total_s = (event["terminal_time"] - event["enqueue_time"]).total_seconds()
        terminal_type_counts[event["terminal_type"]] += 1
        if total_s > 0:
            repo_all_times[event["repo"]].append(total_s)
            if event["terminal_type"] == "merged":
                repo_merged_times[event["repo"]].append(total_s)

    print(f"\n  Terminal type distribution:")
    for ttype, count in sorted(terminal_type_counts.items(), key=lambda x: -x[1]):
        print(f"    {ttype}: {count}")

    ci_estimates: dict[str, float] = {}
    for repo in repo_all_times:
        merged_times = repo_merged_times.get(repo, [])
        if len(merged_times) >= 5:
            p25 = percentile(merged_times, 25)
            ci_estimates[repo] = p25
        elif merged_times:
            ci_estimates[repo] = min(merged_times)
        else:
            ci_estimates[repo] = 15 * 60  # 15 min fallback

    return ci_estimates


def compute_timeline(
    queue_events: list[dict[str, Any]],
    ci_estimates: dict[str, float],
) -> list[dict[str, Any]]:
    """Compute per-event metrics using estimated CI times.

    For each event:
      total_time = terminal_time - enqueue_time  (directly observed)
      processing_time = estimated CI time for repo
      queue_wait = total_time - processing_time  (floored at 0)
    """
    timeline = []

    for event in queue_events:
        enqueue_time = event["enqueue_time"]
        terminal_time = event["terminal_time"]
        total_time_s = (terminal_time - enqueue_time).total_seconds()

        if event["is_effort"] and event["effort_size"] > 1:
            # Effort processing time is the max CI time across repos involved
            ci_time_s = max(
                ci_estimates.get(r, 15 * 60) for r in event["effort_repos"]
            )
        else:
            ci_time_s = ci_estimates.get(event["repo"], 15 * 60)

        processing_time_s = min(ci_time_s, total_time_s)
        queue_wait_s = max(0, total_time_s - processing_time_s)

        timeline.append(
            {
                **event,
                "processing_time_s": processing_time_s,
                "queue_wait_s": queue_wait_s,
                "total_time_s": total_time_s,
            }
        )

    return timeline


def compute_summary(
    timeline: list[dict[str, Any]],
    ci_estimates: dict[str, float],
) -> dict[str, Any]:
    """Compute summary statistics from the timeline."""
    if not timeline:
        return {"error": "No events to summarize"}

    wait_times = [e["queue_wait_s"] for e in timeline]
    processing_times = [e["processing_time_s"] for e in timeline]
    total_times = [e["total_time_s"] for e in timeline]

    repo_counts: dict[str, int] = defaultdict(int)
    repo_processing: dict[str, float] = defaultdict(float)
    repo_wait: dict[str, list[float]] = defaultdict(list)
    terminal_type_counts: dict[str, int] = defaultdict(int)
    effort_counts = {"standalone": 0, "effort": 0}

    for event in timeline:
        repo_counts[event["repo"]] += 1
        repo_processing[event["repo"]] += event["processing_time_s"]
        repo_wait[event["repo"]].append(event["queue_wait_s"])
        terminal_type_counts[event["terminal_type"]] += 1
        if event["is_effort"]:
            effort_counts["effort"] += 1
        else:
            effort_counts["standalone"] += 1

    total_processing = sum(repo_processing.values())

    date_range_days = (
        timeline[-1]["terminal_time"] - timeline[0]["enqueue_time"]
    ).total_seconds() / 86400

    return {
        "total_events": len(timeline),
        "date_range_days": round(date_range_days, 1),
        "ci_estimates_min": {
            repo: round(s / 60, 1) for repo, s in sorted(ci_estimates.items())
        },
        "queue_wait": {
            "mean_s": round(sum(wait_times) / len(wait_times), 1),
            "median_s": round(percentile(wait_times, 50), 1),
            "p95_s": round(percentile(wait_times, 95), 1),
            "max_s": round(max(wait_times), 1),
            "mean_min": round(sum(wait_times) / len(wait_times) / 60, 1),
            "median_min": round(percentile(wait_times, 50) / 60, 1),
            "p95_min": round(percentile(wait_times, 95) / 60, 1),
            "max_min": round(max(wait_times) / 60, 1),
        },
        "processing_time": {
            "mean_s": round(sum(processing_times) / len(processing_times), 1),
            "median_s": round(percentile(processing_times, 50), 1),
            "p95_s": round(percentile(processing_times, 95), 1),
            "max_s": round(max(processing_times), 1),
            "mean_min": round(
                sum(processing_times) / len(processing_times) / 60, 1
            ),
            "median_min": round(percentile(processing_times, 50) / 60, 1),
            "p95_min": round(percentile(processing_times, 95) / 60, 1),
        },
        "total_time": {
            "mean_min": round(sum(total_times) / len(total_times) / 60, 1),
            "median_min": round(percentile(total_times, 50) / 60, 1),
            "p95_min": round(percentile(total_times, 95) / 60, 1),
            "max_min": round(max(total_times) / 60, 1),
        },
        "repo_breakdown": {
            repo: {
                "count": repo_counts[repo],
                "pct_of_total": round(
                    repo_counts[repo] / len(timeline) * 100, 1
                ),
                "ci_estimate_min": round(
                    ci_estimates.get(repo, 15 * 60) / 60, 1
                ),
                "processing_hours": round(
                    repo_processing[repo] / 3600, 1
                ),
                "pct_of_processing": round(
                    repo_processing[repo] / total_processing * 100, 1
                )
                if total_processing > 0
                else 0,
                "mean_wait_min": round(
                    sum(repo_wait[repo]) / len(repo_wait[repo]) / 60, 1
                )
                if repo_wait[repo]
                else 0,
            }
            for repo in sorted(
                repo_counts.keys(), key=lambda r: repo_counts[r], reverse=True
            )
        },
        "terminal_types": dict(terminal_type_counts),
        "efforts": {
            **effort_counts,
            "effort_pct": round(
                effort_counts["effort"]
                / (effort_counts["standalone"] + effort_counts["effort"])
                * 100,
                1,
            )
            if (effort_counts["standalone"] + effort_counts["effort"]) > 0
            else 0,
        },
    }


def write_csv(
    timeline: list[dict[str, Any]], output_dir: str
) -> str:
    filepath = os.path.join(output_dir, "queue_events.csv")
    fieldnames = [
        "enqueue_time",
        "terminal_time",
        "terminal_type",
        "repo",
        "pr_number",
        "branch",
        "queue_wait_s",
        "processing_time_s",
        "total_time_s",
        "is_effort",
        "effort_size",
        "effort_repos",
    ]

    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for event in timeline:
            row = {
                "enqueue_time": event["enqueue_time"].isoformat(),
                "terminal_time": event["terminal_time"].isoformat(),
                "terminal_type": event["terminal_type"],
                "repo": event["repo"],
                "pr_number": event["pr_number"],
                "branch": event["branch"],
                "queue_wait_s": round(event["queue_wait_s"], 1),
                "processing_time_s": round(event["processing_time_s"], 1),
                "total_time_s": round(event["total_time_s"], 1),
                "is_effort": event["is_effort"],
                "effort_size": event["effort_size"],
                "effort_repos": ";".join(event.get("effort_repos", [])),
            }
            writer.writerow(row)

    return filepath


def write_summary(summary: dict[str, Any], output_dir: str) -> str:
    filepath = os.path.join(output_dir, "queue_timeline_summary.txt")

    lines = [
        "=" * 60,
        "MERGE QUEUE TIMELINE SUMMARY",
        "=" * 60,
        "",
        f"Total queue events: {summary['total_events']}",
        f"Date range: {summary['date_range_days']} days",
        "",
        "--- Estimated CI Processing Times (P25 of merged-only shipit time) ---",
    ]
    for repo, ci_min in sorted(
        summary["ci_estimates_min"].items(), key=lambda x: -x[1]
    ):
        lines.append(f"  {repo}: {ci_min} min")

    lines.extend([
        "",
        "--- Queue Wait Time (total_time - ci_estimate) ---",
        f"  Mean:   {summary['queue_wait']['mean_min']} min",
        f"  Median: {summary['queue_wait']['median_min']} min",
        f"  P95:    {summary['queue_wait']['p95_min']} min",
        f"  Max:    {summary['queue_wait']['max_min']} min",
        "",
        "--- Processing Time (estimated CI time, capped by total time) ---",
        f"  Mean:   {summary['processing_time']['mean_min']} min",
        f"  Median: {summary['processing_time']['median_min']} min",
        f"  P95:    {summary['processing_time']['p95_min']} min",
        "",
        "--- Total Shipit Time (enqueue -> terminal, directly observed) ---",
        f"  Mean:   {summary['total_time']['mean_min']} min",
        f"  Median: {summary['total_time']['median_min']} min",
        f"  P95:    {summary['total_time']['p95_min']} min",
        f"  Max:    {summary['total_time']['max_min']} min",
        "",
        "--- Repository Breakdown ---",
    ])

    for repo, stats in summary["repo_breakdown"].items():
        lines.append(
            f"  {repo}: {stats['count']} events ({stats['pct_of_total']}%), "
            f"CI ~{stats['ci_estimate_min']}min, "
            f"mean wait {stats['mean_wait_min']}min"
        )

    lines.extend([
        "",
        "--- Terminal Types ---",
    ])
    for ttype, count in sorted(
        summary["terminal_types"].items(), key=lambda x: -x[1]
    ):
        lines.append(f"  {ttype}: {count}")

    lines.extend([
        "",
        "--- Efforts ---",
        f"  Standalone: {summary['efforts']['standalone']}",
        f"  Cross-repo efforts: {summary['efforts']['effort']} ({summary['efforts']['effort_pct']}%)",
    ])

    text = "\n".join(lines) + "\n"

    with open(filepath, "w") as f:
        f.write(text)

    return filepath


def main():
    parser = argparse.ArgumentParser(
        description="Reconstruct merge queue timeline from shipit events"
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=".",
        help="Directory containing shipit_data_*.json files (default: .)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=".",
        help="Output directory for CSV and summary (default: .)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Queue Timeline Analyzer")
    print("=" * 60)

    print("\nLoading cached data...")
    all_repo_data = load_repo_data(args.data_dir)
    if not all_repo_data:
        print("No shipit_data_*.json files found. Run fetch_shipit_data.py first.")
        sys.exit(1)

    print(f"\nExtracting queue events...")
    queue_events = extract_queue_events(all_repo_data)
    print(f"  {len(queue_events)} queue events extracted")

    if not queue_events:
        print("No queue events found. Check that fetch data contains shipit events.")
        sys.exit(1)

    print(f"\nEstimating CI processing times per repo...")
    ci_estimates = estimate_ci_times(queue_events)
    for repo, ci_s in sorted(ci_estimates.items(), key=lambda x: -x[1]):
        print(f"  {repo}: {ci_s/60:.1f} min (P25 of merged-only shipit times)")

    print(f"\nComputing timeline with estimated CI times...")
    timeline = compute_timeline(queue_events, ci_estimates)

    print(f"\nComputing summary statistics...")
    summary = compute_summary(timeline, ci_estimates)

    csv_path = write_csv(timeline, args.output_dir)
    print(f"\nCSV written to: {csv_path}")

    summary_path = write_summary(summary, args.output_dir)
    print(f"Summary written to: {summary_path}")

    summary_json_path = os.path.join(args.output_dir, "queue_timeline_summary.json")
    with open(summary_json_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Summary JSON written to: {summary_json_path}")

    ci_path = os.path.join(args.output_dir, "ci_estimates.json")
    with open(ci_path, "w") as f:
        json.dump(
            {repo: round(s, 1) for repo, s in ci_estimates.items()},
            f,
            indent=2,
        )
    print(f"CI estimates written to: {ci_path}")

    print(f"\n{'='*60}")
    print("SUMMARY")
    print("=" * 60)
    with open(summary_path) as f:
        print(f.read())

    print(f"\nNext step: python simulate_separate_queues.py")


if __name__ == "__main__":
    main()
