# Cross-Repo Branch Match Analysis

**Author**: Willis Kirkham  
**Analysis Date**: February 8, 2026  
**Data Period**: February 2025 - February 2026 (52 weeks)

## Executive Summary

Approximately **14.4% of all PRs** across `tactivos/murally` and `tactivos/mural-api` were part of a cross-repo effort — a PR in one repo with a matching branch name in the other.

Over the last year, **482 unique branch names appeared in both repos**, representing at least 964 PRs (one in each repo per branch) out of 6,682 total.

| Metric | Value |
|--------|-------|
| Total murally PRs analyzed | 4,301 |
| Total mural-api PRs analyzed | 2,381 |
| Total PRs (combined) | 6,682 |
| Branch names appearing in both repos | 482 |
| PRs involved in cross-repo efforts | ~964 (14.4%) |

---

## The Question

**How often do PRs in murally and mural-api share the same branch name?** This determines how frequently developers work on features that span both repos simultaneously — a key input for environment deduplication, provisioning strategies, and cross-repo coordination tooling.

---

## Key Findings

### Cross-Repo Branch Matches by Window Size

Two PRs (one from each repo) are classified as "matched" when they share the same branch name and were created within N days of each other. Branches outside the window are treated as coincidental naming.

| Window | Matched Branches | Coincidental |
|--------|:---:|:---:|
| 1 day | 408 | 74 |
| 2 days | 427 | 55 |
| **3 days** | **440** | **42** |
| 4 days | 448 | 34 |
| 5 days | 453 | 29 |
| 6 days | 458 | 24 |
| 7 days | 463 | 19 |
| 8 days | 467 | 15 |

**The total (482) is constant across all window sizes** — it represents every branch name that appeared in both repos during the year. The window only determines how many are classified as matched vs coincidental.

### Diminishing Returns Beyond 3 Days

The jump from 1-day to 3-day window captures 32 additional matches (408 → 440), while expanding from 3-day to 8-day only captures 27 more (440 → 467). A 3-day window is the practical sweet spot for matching accuracy.

---

## Supporting Analysis

### Weekly Cross-Repo Match Volume

Cross-repo matches are distributed consistently throughout the year with no seasonal spikes:

| Metric | Value |
|--------|-------|
| Average cross-repo matches per week | 8.1 |
| Peak cross-repo matches in a single week | 15 (week of Apr 7, 2025) |
| Minimum cross-repo matches in a week | 0 (holiday week Dec 29, 2025) |
| Weeks with 10+ cross-repo matches | 15 of 53 weeks |

### Typical Cross-Repo Match Profile

The `matched_branches_sample` data shows most cross-repo branches are created within 0-1 days of each other:

- **0-day delta**: Branches created the same day in both repos (most common)
- **1-day delta**: Branch created in one repo, then the other the next day (very common)
- **2-3 day delta**: Less common, typically larger features where backend work starts after frontend

---

## Methodology

### Data Collection

PR data was fetched from GitHub's GraphQL API for both `tactivos/murally` and `tactivos/mural-api` covering February 3, 2025 through February 3, 2026. Fields collected: PR number, branch name (`headRefName`), `createdAt`, `closedAt`, `mergedAt`, and state.

### Matching Logic

1. Group PRs by branch name across both repos
2. For each branch name appearing in both repos, compare the earliest `createdAt` from each repo
3. If the delta is within the configured window (N days), classify as **matched**
4. If the delta exceeds the window, classify as **coincidental naming**

### Edge Cases

| Case | Handling |
|------|----------|
| Multiple PRs with the same branch in one repo | Uses the earliest creation date |
| Branch exists in only one repo | Not counted (no cross-repo match possible) |
| Same branch name, >N days apart | Treated as coincidental naming, counted separately |

### Scripts

All scripts and data are in `deduplicate_branch_analysis/`:

| Script | Purpose |
|--------|---------|
| `fetch_pr_data.py` | Fetches PR data via GitHub GraphQL API |
| `analyze_branches.py` | Cross-repo branch matching and weekly statistics |
| `analyze_concurrency.py` | Concurrent environment simulation (used in [PR Volume Risk Analysis](pr-volume-risk-analysis.md)) |

### Reproducing the Analysis

```bash
cd deduplicate_branch_analysis/

# Fetch PR data (uses cached data if available)
python fetch_pr_data.py

# Run analysis for a specific window size
python analyze_branches.py --window-days 3

# Run for all window sizes
for n in 1 2 3 4 5 6 7 8; do
  python analyze_branches.py --window-days $n
done
```