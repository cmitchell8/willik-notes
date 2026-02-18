# Merge Queue Separation Analysis

Scripts to model the impact of separating the `mural-web` shipit2 merge queue into per-repo queues.

## Background

The `mural-web` shipit2 queue processes ~15 repos through a single FIFO queue. This analysis quantifies how much faster PRs would merge if murally and mural-api (and all other repos) had their own parallel queues.

## Setup

```bash
# Use the existing venv from prior analysis, or create a new one
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# GitHub token for API access
export GITHUB_TOKEN=$(gh auth token)
```

## Scripts

Run in order:

### Step 0: `discover_shipit_bot.py`

Samples recent PRs to identify the shipit bot username and catalog all comment formats it uses. Outputs `bot_discovery.json`.

```bash
python discover_shipit_bot.py

# Options:
#   --sample-size 50    Number of PRs to sample for bot discovery
#   --audit-size 200    Number of PRs to check for format drift
#   --force             Re-run even if cached
```

### Step 1: `fetch_shipit_data.py`

Fetches merged/closed PRs with all bot comments from GitHub GraphQL API. Caches per-repo JSON files. Classification is done after collection so heuristics can be refined without re-fetching.

```bash
python fetch_shipit_data.py

# Options:
#   --repos murally mural-api    Specific repos (default: all 15 mural-web repos)
#   --start-date 2025-02-18      Start date (default: 2025-02-18)
#   --end-date 2026-02-18        End date (default: 2026-02-18)
#   --bot-username USER           Override bot username from discovery
#   --force                       Re-fetch even if cached
```

### Step 2: `analyze_queue_timeline.py`

Reconstructs the single-queue FIFO timeline from shipit events. Groups cross-repo efforts as single queue items. Computes per-item queue wait time, processing time, and total time.

```bash
python analyze_queue_timeline.py

# Options:
#   --data-dir .      Directory with shipit_data_*.json files
#   --output-dir .    Directory for output files
```

**Outputs:**
- `queue_events.csv` — per-event timeline with metrics
- `queue_timeline_summary.txt` — human-readable summary
- `queue_timeline_summary.json` — machine-readable summary

### Step 3: `simulate_separate_queues.py`

Simulates per-repo queues vs the current single queue. Cross-repo efforts are expanded into separate queue entries. Compares wait times and computes improvement metrics.

```bash
python simulate_separate_queues.py

# Options:
#   --data-dir .      Directory with queue_events.csv
#   --output-dir .    Directory for output files
```

**Outputs:**
- `simulation_results.csv` — per-PR current vs proposed comparison
- `weekly_summary.csv` — weekly aggregated metrics
- `wait_time_distribution.csv` — histogram of wait times
- `queue_depth_timeseries.csv` — hourly queue depth by repo
- `simulation_comparison.json` — full comparison metrics

## Output Files

| File | Description |
|------|-------------|
| `bot_discovery.json` | Bot username and comment format catalog |
| `shipit_data_*.json` | Raw cached PR data per repo |
| `queue_events.csv` | Reconstructed timeline with per-event metrics |
| `queue_timeline_summary.json` | Summary statistics for the single queue |
| `simulation_results.csv` | Per-PR current vs proposed comparison |
| `weekly_summary.csv` | Weekly aggregated metrics |
| `wait_time_distribution.csv` | Histogram of wait times (current vs proposed) |
| `queue_depth_timeseries.csv` | Hourly queue depth by repo |
| `simulation_comparison.json` | Full comparison metrics |

## Analysis Document

Results are summarized in `../merge-queue-separate-queues-analysis.md`.
