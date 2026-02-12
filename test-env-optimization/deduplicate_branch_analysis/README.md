# PR Branch Deduplication Analysis

Analyze PR data from `tactivos/murally` and `tactivos/mural-api` to identify cross-repo branch matches and calculate deduplicated test environment estimates.

## Background

When developers work on features spanning both frontend (murally) and backend (mural-api), they typically use the **same branch name** in both repositories. The test environment system creates **only one environment per unique branch name**, so counting PRs independently overcounts the actual number of test environments needed.

This tool:
1. Fetches PR data from both repositories
2. Identifies branch names that appear in both repos within a configurable time window
3. Calculates the deduplicated count of unique test environments
4. Outputs weekly statistics and sample matched branches for validation

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Set GitHub token (one of these methods)
export GITHUB_TOKEN=$(gh auth token)
# or
export GITHUB_TOKEN=ghp_your_token_here
```

## Usage

### Step 1: Fetch PR Data (One Time)

Fetch and cache PR data from both repositories:

```bash
python fetch_pr_data.py
```

Options:
- `--start YYYY-MM-DD`: Start date (default: 2025-02-03)
- `--end YYYY-MM-DD`: End date (default: 2026-02-03)
- `--output-dir DIR`: Output directory for JSON files (default: current)
- `--force`: Force re-fetch even if cached data exists

This creates cached data files:
- `pr_data_murally.json`
- `pr_data_mural-api.json`

**Note:** If cache files already exist, the script will skip fetching and inform you. Use `--force` to re-fetch.

### Step 2: Run Analysis (Repeatable with Different Parameters)

Analyze the cached data to find cross-repo matches:

```bash
python analyze_branches.py
```

Options:
- `--window-days N`: Time window for matching (default: 3 days)
- `--data-dir DIR`: Directory containing cached JSON files (default: current)
- `--output-dir DIR`: Output directory for results (default: current)

This creates (with window size in filename):
- `weekly_analysis_3days.csv`: Weekly breakdown of PRs and environments
- `matched_branches_sample_3days.txt`: Sample of matched branches for validation

### Experimenting with Different Window Sizes

The main benefit of caching is that you can run analysis multiple times with different window sizes without re-fetching data from GitHub:

```bash
# Fetch data once
python fetch_pr_data.py

# Try different window sizes
python analyze_branches.py --window-days 1
python analyze_branches.py --window-days 3
python analyze_branches.py --window-days 5
python analyze_branches.py --window-days 7
python analyze_branches.py --window-days 14
```

Each run creates uniquely named output files:
- `weekly_analysis_1days.csv`, `matched_branches_sample_1days.txt`
- `weekly_analysis_3days.csv`, `matched_branches_sample_3days.txt`
- etc.

This lets you compare how the deduplication percentage changes based on the matching window.

## Output Files

Output files include the window size in the filename (e.g., `_3days`) to allow comparison across different window settings.

### weekly_analysis_{N}days.csv

Weekly statistics with columns:
- `week_start`: Monday of the week
- `murally_prs`: Number of PRs in murally
- `api_prs`: Number of PRs in mural-api
- `combined_prs`: Total PRs
- `murally_branches`: Unique branches in murally
- `api_branches`: Unique branches in mural-api
- `cross_repo_matches`: Branches matched across repos
- `deduplicated_envs`: Actual test environments needed

### matched_branches_sample_{N}days.txt

Sample of cross-repo matches showing:
- Branch name
- PR numbers and dates from each repo
- Time delta between PRs
- Whether it's a match (within window) or coincidental naming

## Deduplication Logic

Two PRs (one from each repo) are considered "the same effort" and deduplicated if:
1. They have **exactly the same branch name** (case-sensitive)
2. Their creation dates are **within N days of each other** (default: 3 days)

### Edge Cases

1. **Multiple PRs same branch, same repo**: Uses earliest creation date
2. **Branch in one repo only**: Counted as one environment
3. **Same branch name, >N days apart**: Treated as separate efforts (coincidental naming), counted as two environments

## Example Output

```
============================================================
SUMMARY STATISTICS
============================================================

-----------------------PR Counts-----------------------
Total PRs (murally):           4,304
Total PRs (mural-api):         2,390
Total PRs (combined):          6,694

--------------------Branch Analysis--------------------
Unique branches (murally):     3,850
Unique branches (mural-api):   2,100
Branches in both repos:          450

----------Cross-Repo Matching (window: 3 days)----------
Cross-repo matches:              380
Coincidental naming (>window):    70

-----------------Deduplication Results-----------------
Branches only in murally:      3,400
Branches only in mural-api:    1,650
Matched cross-repo branches:     380
Non-matched (counted twice):      70

Naive branch count:            5,950
Deduplicated env count:        5,570
Reduction from dedup:            380 (6.4%)
```

## Concurrent Environment Analysis

While `analyze_branches.py` calculates **total environments created per week**, it doesn't account for the fact that test environments are automatically cleaned up when PRs close. The actual infrastructure load depends on **concurrent open PRs**, not total created.

`analyze_concurrency.py` extends the analysis to calculate concurrent environment counts over time.

### Running Concurrency Analysis

First, ensure you have PR data with close times:

```bash
# Re-fetch data to include closedAt/mergedAt fields
python fetch_pr_data.py --force

# Run concurrency analysis (hourly resolution - more accurate, slower)
python analyze_concurrency.py

# Or with daily resolution (faster, less granular)
python analyze_concurrency.py --resolution daily
```

Options:
- `--resolution hourly|daily`: Time resolution (default: hourly)
- `--data-dir DIR`: Directory containing cached JSON files (default: current)
- `--output-dir DIR`: Output directory for results (default: current)

### Concurrency Output Files

#### concurrent_envs_timeseries.csv

Time series data with concurrent environment counts:
- `timestamp`: ISO timestamp of the data point
- `murally_active`: PRs with open status in murally at this time
- `api_active`: PRs with open status in mural-api at this time
- `naive_total`: Sum of murally + api (without deduplication)
- `deduplicated_envs`: Unique branch names (with cross-repo deduplication)
- `cross_repo_active`: Branches with open PRs in both repos simultaneously

#### concurrent_envs_summary.txt

Summary statistics including:
- PR lifespan statistics (median, P90, P95 by repository)
- Concurrent environment statistics (peak, average, P95)
- Peak timestamp
- Cross-repo concurrency metrics
- Long-lived PRs (>30 days open)

#### pr_lifespan_distribution.csv

Histogram of PR lifespans:
- `lifespan_bucket`: Time range (0-1h, 1-2h, 2-4h, etc.)
- `murally_count`: Number of murally PRs in this bucket
- `api_count`: Number of mural-api PRs in this bucket

### Concurrency Deduplication Logic

For concurrent environments, deduplication is straightforward:
- At any time point T, if branch "feature/foo" has an open PR in **both** repos, count as **1 environment**
- No time-window matching needed—if both PRs are open at time T, they share an environment

### Edge Cases

1. **Still-open PRs**: Treated as open until the analysis end time
2. **Very long-lived PRs**: Flagged in summary if open >30 days (potential outliers)
3. **Draft PRs**: Included in analysis (would get environments with auto-provisioning)

### Example Concurrency Output

```
============================================================
CONCURRENT ENVIRONMENT ANALYSIS
============================================================

PR Lifespan Statistics:
------------------------------
Murally PRs analyzed:          4,301
  Median lifespan:             8.2 hours
  90th percentile:             72.5 hours
  95th percentile:             168.0 hours

mural-api PRs analyzed:        2,390
  Median lifespan:             12.4 hours
  90th percentile:             96.3 hours
  95th percentile:             240.0 hours

Concurrent Environment Statistics:
------------------------------
Peak concurrent (naive):       142
Peak concurrent (deduplicated):135
Average concurrent:            89.3
Median concurrent (P50):       85.0
P95 concurrent:                120.0

Peak occurred at:              2025-06-15T14:00:00

Cross-Repo Concurrency:
------------------------------
Max branches active in both:   12
Avg branches active in both:   4.2
Deduplication savings:         5.2% reduction
```

## Notes

- GraphQL API is used for efficient data fetching (better than REST for this use case)
- Data is cached locally to allow re-running analysis with different parameters
- GitHub API rate limit: 5,000 requests/hour for authenticated users
- Estimated requests needed: ~15-20 (paginated, 100 PRs per page)
- The fetch script now includes `closedAt` and `mergedAt` fields for concurrency analysis
- Hourly resolution provides more accurate peak detection but takes longer to compute
