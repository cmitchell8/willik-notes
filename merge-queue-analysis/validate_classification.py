#!/usr/bin/env python3
"""
Validate the deterministic classifier by exporting PRs for manual review.

Exports the most recent N PRs with their deterministic classification
in a compact JSON format suitable for LLM or human review.

Usage:
    source venv/bin/activate

    # Export last 1000 PRs (default)
    python validate_classification.py

    # Export a different count
    python validate_classification.py --count 500

    # Print compact summary per bucket
    python validate_classification.py --print-buckets
"""

import argparse
import json
import sys
from classify_modularization_prs import classify_pr

SCRIPT_DIR = __import__("os").path.dirname(__import__("os").path.abspath(__file__))
PR_CACHE = __import__("os").path.join(SCRIPT_DIR, "murally_prs_metadata.json")


def main():
    parser = argparse.ArgumentParser(description="Export PRs for classification validation")
    parser.add_argument("--count", type=int, default=1000, help="Number of most recent PRs to export (default: 1000)")
    parser.add_argument("--print-buckets", action="store_true", help="Print compact per-bucket summaries to stdout")
    parser.add_argument("--output", type=str, default="last_{count}_for_review.json", help="Output filename (default: last_{count}_for_review.json)")
    args = parser.parse_args()

    prs = json.load(open(PR_CACHE))
    prs.sort(key=lambda p: p["merged_at"], reverse=True)
    recent = prs[: args.count]

    for p in recent:
        p["bucket"] = classify_pr(p)

    buckets = {"yes": [], "no": [], "ambiguous": []}
    for p in recent:
        buckets[p["bucket"]].append(p)

    print(f"Last {args.count} PRs: {len(buckets['yes'])} yes, {len(buckets['no'])} no, {len(buckets['ambiguous'])} ambiguous")

    if args.print_buckets:
        for bucket_name in ("yes", "ambiguous", "no"):
            items = buckets[bucket_name]
            print(f"\n{'='*70}")
            print(f"{bucket_name.upper()} bucket: {len(items)} PRs")
            print(f"{'='*70}\n")
            for p in items:
                num = p["number"]
                branch = p["branch"][:55]
                title = p["title"][:85]
                files = p["changed_files"]
                body = (p.get("body", "") or "")[:200].replace("\n", " ").replace("\r", "").strip()
                print(f"#{num} | {branch} | {title}")
                print(f"  files={files} | body: {body}")
                print()
        return

    output = []
    for p in recent:
        output.append(
            {
                "number": p["number"],
                "bucket": p["bucket"],
                "title": p["title"],
                "branch": p["branch"],
                "changed_files": p["changed_files"],
                "additions": p["additions"],
                "deletions": p["deletions"],
                "body_preview": (p.get("body") or "")[:400],
            }
        )

    outfile = args.output.replace("{count}", str(args.count))
    outpath = __import__("os").path.join(SCRIPT_DIR, outfile)
    with open(outpath, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Written to {outpath}")


if __name__ == "__main__":
    main()
