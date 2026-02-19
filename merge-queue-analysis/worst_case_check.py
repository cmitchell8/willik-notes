#!/usr/bin/env python3
"""
Compute worst-case queue statistics assuming all ambiguous PRs are modularization.

Loads the cached PR metadata, runs the deterministic classifier, treats
all clear-YES and all ambiguous PRs as modularization, then cross-references
against queue_events.csv to produce recomputed queue statistics.

Usage:
    source venv/bin/activate
    python worst_case_check.py
"""

import csv
import json
import os

from classify_modularization_prs import classify_pr

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PR_CACHE = os.path.join(SCRIPT_DIR, "murally_prs_metadata.json")
QUEUE_EVENTS = os.path.join(SCRIPT_DIR, "queue_events.csv")


def main():
    prs = json.load(open(PR_CACHE))
    print(f"{len(prs)} total PRs loaded")

    yes_nums = set()
    ambig_nums = set()
    for p in prs:
        b = classify_pr(p)
        if b == "yes":
            yes_nums.add(p["number"])
        elif b == "ambiguous":
            ambig_nums.add(p["number"])

    worst_case_mod = yes_nums | ambig_nums
    print(f"Clear YES: {len(yes_nums)}, Ambiguous: {len(ambig_nums)}, Worst-case total: {len(worst_case_mod)}")

    events = list(csv.DictReader(open(QUEUE_EVENTS)))
    total = len(events)
    murally_events = [e for e in events if e["repo"] == "murally"]
    effort_events = [e for e in events if e["is_effort"] == "True"]
    mural_api_events = [e for e in events if e["repo"] == "mural-api"]

    mod_events = [e for e in murally_events if int(e["pr_number"]) in worst_case_mod]
    mod_count = len(mod_events)

    ex_total = total - mod_count
    ex_murally = len(murally_events) - mod_count

    def pct(n, d):
        return f"{n / d * 100:.1f}%" if d > 0 else "N/A"

    print(f"\n--- Queue event stats ---")
    print(f"Total events:       {total}")
    print(f"murally events:     {len(murally_events)}")
    print(f"Mod queue events:   {mod_count} (worst case)")
    print(f"Effort events:      {len(effort_events)}")
    print(f"mural-api events:   {len(mural_api_events)}")
    print()
    print(f"--- Original ---")
    print(f"murally share:      {pct(len(murally_events), total)}")
    print(f"mural-api share:    {pct(len(mural_api_events), total)}")
    print(f"Effort %:           {pct(len(effort_events), total)}")
    print()
    print(f"--- Worst case (exclude ALL {mod_count} mod events) ---")
    print(f"murally share:      {pct(ex_murally, ex_total)}")
    print(f"mural-api share:    {pct(len(mural_api_events), ex_total)}")
    print(f"Effort %:           {pct(len(effort_events), ex_total)}")
    print(
        f"Change in effort %: {pct(len(effort_events), total)} -> {pct(len(effort_events), ex_total)} "
        f"(delta: {(len(effort_events) / ex_total - len(effort_events) / total) * 100:.1f}pp)"
    )


if __name__ == "__main__":
    main()
