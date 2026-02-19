#!/usr/bin/env python3
"""
Print a compact, human-readable view of PRs within a specified bucket
from the validation export file.

Usage:
    source venv/bin/activate

    # Show all YES-bucket PRs
    python review_buckets.py yes

    # Show all ambiguous PRs
    python review_buckets.py ambiguous

    # Show NO-bucket PRs (first 50 by default)
    python review_buckets.py no

    # Show more NO-bucket PRs
    python review_buckets.py no --limit 200

    # Use a custom input file
    python review_buckets.py yes --input custom_file.json
"""

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = os.path.join(SCRIPT_DIR, "last_1000_for_review.json")


def main():
    parser = argparse.ArgumentParser(description="Print PRs from a specific classification bucket")
    parser.add_argument("bucket", choices=["yes", "no", "ambiguous"], help="Which bucket to display")
    parser.add_argument("--input", type=str, default=DEFAULT_INPUT, help="Input JSON file (default: last_1000_for_review.json)")
    parser.add_argument("--limit", type=int, default=50, help="Max PRs to display (default: 50, use -1 for all)")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: {args.input} not found. Run validate_classification.py first.")
        sys.exit(1)

    data = json.load(open(args.input))
    filtered = [p for p in data if p["bucket"] == args.bucket]
    print(f"{args.bucket.upper()} bucket: {len(filtered)} PRs\n")

    limit = len(filtered) if args.limit == -1 else args.limit
    for p in filtered[:limit]:
        num = p["number"]
        title = p["title"][:85]
        branch = p["branch"][:55]
        files = p["changed_files"]
        body = p.get("body_preview", "")[:200].replace("\n", " ").replace("\r", "").strip()
        print(f"#{num} | {branch} | {title}")
        print(f"  files={files} adds={p.get('additions',0)} dels={p.get('deletions',0)}")
        if body:
            print(f"  body: {body}")
        print()

    if len(filtered) > limit:
        print(f"... showing {limit} of {len(filtered)} (use --limit -1 to see all)")


if __name__ == "__main__":
    main()
