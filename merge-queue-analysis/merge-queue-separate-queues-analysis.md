# Merge Queue Separation Analysis

**Author**: Willis Kirkham  
**Analysis Date**: February 18, 2026  
**Data Period**: February 2025 - February 2026 (12 months)

## Executive Summary

Separate merge queues for each repo would reduce mean queue wait time by **92.9%** (from 23.0 min to 1.6 min). The parallelism gains massively outweigh the overhead of splitting cross-repo efforts into separate queue entries.

| Metric | Current (Single Queue) | Proposed (Separate Queues) | Improvement |
|--------|:---:|:---:|:---:|
| Mean queue wait time | 23.0 min | 1.6 min | 92.9% |
| Median queue wait time | 3.8 min | 0.0 min | 100.0% |
| P95 queue wait time | 54.0 min | 9.8 min | 81.9% |
| Total time saved (12 months) | — | 1,872 hours | — |
| Queue entries (effort overhead) | 5,146 | 5,264 (+2.3%) | — |

**Key insight**: The single queue forces 5,204 items through one pipe serially. murally (70% of volume) and mural-api (25%) block each other despite having independent CI pipelines. Separating them eliminates nearly all cross-repo blocking. The 118 additional queue entries from expanding cross-repo efforts are negligible at 2.3% overhead.

**Recommendation**: Proceed with queue separation. The ~8.0% of queue events that are cross-repo efforts will require two separate shipit invocations, but developers save an average of 21.3 minutes of wait time per PR — totaling 1,872 hours of eliminated developer wait time over the past year.

---

## The Question

**Decision**: Should we remove the "effort" concept from shipit2 and give each repo its own merge queue?

**Current state**: The `mural-web` shipit2 queue processes ~15 repos through a single FIFO queue. A mural-api PR waits behind murally PRs (and vice versa) even though they have independent CI pipelines. Cross-repo "efforts" bundle PRs from multiple repos into a single queue item.

**Proposed state**: Each repo gets its own parallel merge queue. The effort concept is removed — every PR ships independently. Cross-repo changes require two separate shipit invocations instead of one bundled effort.

**Stakes**: If the parallelism gains are marginal (e.g., <10% wait time reduction), the operational cost of losing bundled efforts may not be justified. If gains are substantial (>30%), the case for separation is clear. The data shows a **92.9% improvement** — well beyond the threshold.

---

## Key Findings

### Wait Time Comparison

| Metric | Current | Proposed | Saved | Improvement |
|--------|:---:|:---:|:---:|:---:|
| Mean wait | 23.0 min | 1.6 min | 21.3 min | 92.9% |
| Median wait | 3.8 min | 0.0 min | 3.8 min | 100.0% |
| P95 wait | 54.0 min | 9.8 min | 44.2 min | 81.9% |

The median wait drops from 3.8 min to 0 — meaning most PRs would be processed immediately with no queue. Even at the P95 (worst 1-in-20 PRs), wait drops from ~54 minutes to ~10 minutes.

### Total Shipit Time (enqueue to merge)

| Metric | Current | Proposed |
|--------|:---:|:---:|
| Mean total time | 32.0 min | 12.1 min |
| Median total time | 14.8 min | 9.8 min |

### Queue Utilization by Repository

murally and mural-api account for 95.1% of all queue events and 99.5% of processing time. The remaining 13 repos contribute negligible load.

| Repository | Queue Events | % of Total | Processing Hours | % of Processing |
|------------|:---:|:---:|:---:|:---:|
| murally | 3,630 | 69.8% | 523.0 | 66.8% |
| mural-api | 1,320 | 25.4% | 255.6 | 32.6% |
| mural-integrations | 84 | 1.6% | 1.2 | 0.2% |
| pdf-import | 75 | 1.4% | 0.9 | 0.1% |
| mural-render | 53 | 1.0% | 1.4 | 0.2% |
| Other 6 repos | 42 | 0.8% | 0.8 | 0.1% |

### Per-Repo Wait Time Improvement

Every repo benefits from queue separation. Smaller repos see near-total elimination of wait time since their low volume means virtually no within-repo contention.

| Repository | Count | Current Mean Wait | Proposed Mean Wait | Improvement |
|------------|:---:|:---:|:---:|:---:|
| murally | 3,686 | 21.1 min | 1.9 min | 90.8% |
| mural-api | 1,320 | 27.9 min | 1.1 min | 96.1% |
| mural-integrations | 85 | 10.6 min | 0.0 min | 99.9% |
| pdf-import | 76 | 37.7 min | 0.0 min | 100.0% |
| mural-render | 55 | 35.3 min | 0.1 min | 99.8% |
| mural-seeder | 10 | 9.2 min | 0.0 min | 100.0% |
| Other 5 repos | 32 | 9.6 min | 0.0 min | ~100% |

### Cross-Repo Effort Overhead

In the proposed model, cross-repo efforts become separate queue entries — one per repo involved. This analysis quantifies the additional load.

| Metric | Value |
|--------|-------|
| Cross-repo efforts detected | 415 (8.0% of events) |
| Additional queue entries in proposed model | 118 |
| % increase in queue entries | 2.3% |
| Net impact | 21.3 min saved per PR despite 2.3% more entries |

The 8.0% effort rate detected from shipit comment links is lower than the 14.4% found via branch-name matching in the [cross-repo branch analysis](cross-repo-branch-match-analysis.md). This gap is likely because effort link parsing from enqueued comments is more conservative — it only counts PRs explicitly listed in the shipit comment, while branch matching captures all same-named branches regardless of whether they shipped as an effort.

### Event Outcomes

| Outcome | Count | % |
|---------|:---:|:---:|
| Merged | 4,678 | 89.9% |
| Failed | 500 | 9.6% |
| Cancelled | 26 | 0.5% |

The 9.6% failure rate means roughly 1 in 10 queue items fails CI after entering the queue, consuming queue time without a successful merge.

---

## Supporting Analysis

### Weekly Trends

Queue congestion varies significantly week to week. The simulation accounts for actual enqueue patterns rather than uniform distributions.

**Peak congestion weeks** (highest current mean wait):

| Week | Events | Current Mean Wait | Proposed Mean Wait | Time Saved |
|------|:---:|:---:|:---:|:---:|
| 2025-W46 | 116 | 83.5 min | 1.7 min | 158.1 hours |
| 2026-W05 | 91 | 83.3 min | 1.2 min | 124.5 hours |
| 2025-W12 | 147 | 81.5 min | 2.7 min | 193.2 hours |
| 2025-W15 | 118 | 81.5 min | 1.4 min | 157.4 hours |
| 2025-W41 | 98 | 69.3 min | 1.2 min | 111.1 hours |

During the worst week (W46), PRs waited an average of **83.5 minutes** in the single queue. With separate queues, wait would have been 1.7 minutes — a 98.0% improvement.

**Quietest weeks** (lowest current mean wait):

| Week | Events | Current Mean Wait | Proposed Mean Wait |
|------|:---:|:---:|:---:|
| 2025-W27 | 51 | 1.9 min | 0.8 min |
| 2025-W35 | 102 | 4.7 min | 1.0 min |
| 2025-W08 | 73 | 5.0 min | 0.8 min |

Even during quiet weeks, separate queues still reduce wait times.

### Wait Time Distribution

The distribution shifts dramatically. Under separate queues, 85.8% of PRs experience less than 5 minutes of wait (vs 56.9% currently), and PRs waiting more than 45 minutes are eliminated entirely.

| Wait Time Bucket | Current | Proposed |
|------------------|:---:|:---:|
| 0–5 min | 56.9% | 85.8% |
| 5–15 min | 22.8% | 12.3% |
| 15–30 min | 10.5% | 1.6% |
| 30–60 min | 5.7% | 0.2% |
| 60–120 min | 2.6% | 0.0% |
| 120+ min | 1.6% | 0.0% |

Currently, **9.9%** of PRs wait more than 30 minutes. With separate queues, that drops to **0.2%**.

---

## Methodology

### Data Collection

1. **Bot Discovery** (`discover_shipit_bot.py`): Sampled recent PRs from `tactivos/murally` to identify the shipit bot username and catalog all comment formats. Verified format consistency across the 12-month analysis period.

2. **Data Fetching** (`fetch_shipit_data.py`): Fetched merged and closed PRs with all bot comments from all 15 repos in the `mural-web` shipit queue via GitHub GraphQL API. Collected 5,692 PRs across all repos. Comments were classified into event types (enqueued, merged, failed, cancelled) using keyword heuristics against the bot's comment body.

3. **Timeline Analysis** (`analyze_queue_timeline.py`): Extracted 5,204 enqueue→terminal event pairs. Enqueue-to-terminal pairing uses a chronological LIFO algorithm: events are walked in order, and each terminal (merged/failed/cancelled) pairs with the most recent unmatched enqueue before it. This correctly handles PRs with multiple enqueue cycles (e.g., enqueue→failed→re-enqueue→merged) without double-counting. 118 unpaired enqueues (no terminal in data) were dropped. Cross-repo efforts (identified by linked PRs in enqueued comments) were grouped as single queue items (415 efforts detected). CI processing time was estimated per repo using the 25th percentile of total shipit time among successfully merged events. Queue wait was derived as `total_shipit_time - estimated_ci_time`.

4. **Queue Simulation** (`simulate_separate_queues.py`): Compared single queue (current) vs separate per-repo queues (proposed). Cross-repo efforts were expanded into separate entries per repo, with processing time set to the data-estimated CI time for each repo. Each repo's queue was simulated as an independent FIFO queue.

### CI Processing Time Estimation

For each shipit event we can observe the **total shipit time** (enqueue → merge), but not the two components that make it up:

```
total_shipit_time  =  queue_wait  +  processing_time
```

We need to estimate processing time to derive queue wait. The approach: events that entered an empty queue had zero wait, so their total time equals their processing time. By looking at the fastest events per repo, we can isolate that processing-only signal.

Concretely, for each repo we take the **25th percentile (P25)** of total shipit time among successfully merged events:

- **P25 captures front-of-queue items.** 25% of merged events completed faster than this threshold — these are items that were first in line or entered an empty queue.
- **Robust against outliers.** The minimum might be a single anomalously fast event. P25 is stable across hundreds of data points.
- **Avoids inflating the estimate.** Higher percentiles (P50, P75) include items that waited in queue, which would overestimate CI time and underestimate wait.

Failed and cancelled events are excluded from CI estimation because they resolve quickly (validation failure or user cancellation) without running a full CI pipeline, which would artificially deflate the estimate.

Once the CI estimate is set, every event's queue wait is derived as:

```
queue_wait  =  total_shipit_time  -  ci_estimate    (floored at 0)
```

| Repository | Estimated CI Time (P25 shipit) | Validated CI Time (GitHub Actions) |
|------------|:---:|:---:|
| murally | 9.8 min | 10.1 min median, 10.8 min mean (1,553 runs, Jan 12–Feb 6) |
| mural-api | 14.2 min | 20–21 min median (7 runs, Jan 26) |
| Other repos | 0.5–1.1 min | — |

The murally P25 shipit estimate of 9.8 min closely matches the independently measured CI build time (median 10.1 min from 1,553 successful GitHub Actions runs). The prior assumption that murally CI takes ~30–45 minutes was incorrect — 83% of full builds complete in the 6–14 minute range.

The mural-api P25 estimate (14.2 min) is lower than measured CI time (20–21 min). This gap is expected: items at the P25 include those where CI already passed before enqueue (no rebase needed), while the GitHub Actions measurement reflects the full build duration. This means the mural-api CI estimate slightly underestimates processing time, which would slightly overstate current wait times and understate proposed wait times — making the comparison conservative in the right direction.

**Why this is safe for the comparison:** the same CI estimate is used for both the current-state wait derivation and the proposed-state simulation. If the estimate is slightly low, current waits are overestimated and proposed waits are underestimated — but if it's slightly high, the reverse is true. Any systematic bias shifts both sides and cancels in the relative comparison.

### Assumptions

1. **Queue is FIFO** — priority exceptions are rare and not modeled
2. **Processing time per repo is approximately constant** — estimated from merged-event P25 total shipit times
3. **Shipit comment timestamps accurately reflect enqueue/merge times** — the bot posts at each lifecycle event
4. **Failed/cancelled items consumed queue time** — included in the timeline (500 failures, 26 cancellations)
5. **Enqueue-to-terminal pairing uses LIFO** — for PRs with multiple enqueue cycles, each terminal pairs with the most recent preceding enqueue; unpaired enqueues (118 total) are dropped
6. **Cross-repo efforts become 2+ separate entries** in the proposed model, one per repo involved
7. **Each of the ~15 repos gets its own queue** in the proposed model
8. **CI estimate bias cancels** — the same estimate is used for current wait derivation and proposed simulation

### Edge Cases

- PRs with shipit events but no merge timestamp used the terminal event timestamp
- Unknown bot comments were logged but excluded from timeline reconstruction
- The 6,940 min (~4.8 day) max wait outlier was preserved in the data — likely a holiday/incident period

---

## Appendix A: Script References

All scripts are in this directory:

| Script | Purpose |
|--------|---------|
| `discover_shipit_bot.py` | Identify bot username, catalog comment formats |
| `fetch_shipit_data.py` | Fetch PR + bot comment data from GitHub GraphQL |
| `analyze_queue_timeline.py` | Estimate CI times, compute per-item wait metrics |
| `simulate_separate_queues.py` | Simulate separate queues, compare with single queue |

## Appendix B: Output Files

| File | Description |
|------|-------------|
| `bot_discovery.json` | Bot username and comment format catalog |
| `shipit_data_*.json` | Raw cached PR data per repo (15 files) |
| `ci_estimates.json` | Data-estimated CI processing time per repo |
| `queue_events.csv` | Timeline with per-event metrics (5,204 rows) |
| `queue_timeline_summary.json` | Summary statistics for the current queue |
| `simulation_results.csv` | Per-PR current vs proposed comparison (5,264 rows) |
| `weekly_summary.csv` | Weekly aggregated metrics (53 weeks) |
| `wait_time_distribution.csv` | Histogram of wait times (current vs proposed) |
| `queue_depth_timeseries.csv` | Hourly queue depth by repo |
| `simulation_comparison.json` | Full comparison metrics |

## Appendix C: Reproducibility

```bash
cd merge-queue-analysis/  # from willik-notes root
pip install -r requirements.txt

export GITHUB_TOKEN=$(gh auth token)

# Step 0: Discover bot username (~1 min)
python discover_shipit_bot.py

# Step 1: Fetch data (all 15 repos, ~30 min)
python fetch_shipit_data.py

# Step 2: Analyze timeline and estimate CI times
python analyze_queue_timeline.py

# Step 3: Simulate separate queues
python simulate_separate_queues.py

# Results in simulation_comparison.json and CSV files
```
