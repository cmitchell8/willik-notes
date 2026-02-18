# RFC: Deprecate efforts to allow merge queue per repo

**Status:** Draft

**Author(s):** Willis Kirkham

**Supersedes:** N/A

## Reviewers

| Required Reviewer | Approved | Approval Date |
|---|---|---|
| _TBD_ | Awaiting Review | |

---

## Context

shipit currently processes ~15 repositories through a single shared FIFO merge queue. Cross-repo "efforts" allow developers to bundle PRs from multiple repos into a single queue item so they merge together. Efforts only coordinate merging — there is no deploy-time coordination or ordering guarantee. In practice, this means a mural-api PR waits behind murally PRs — and vice versa — even though they have completely independent CI pipelines.

Over the past 12 months, 5,204 PRs flowed through this queue. murally accounts for 70% of volume and mural-api for 25%. Only 14% of queue events were cross-repo efforts.

## Problem Statement

The single shared queue creates unnecessary developer wait time. A data analysis of 12 months of shipit events (Feb 2025 – Feb 2026) shows developers currently wait an average of **19.9 minutes** per PR in the merge queue, with P95 waits reaching **51 minutes**. During peak weeks, average waits exceeded **80 minutes**. Over the analysis period, this totals approximately **1,700 hours** of developer time spent waiting for the queue.

The root cause is serialization: repos with independent CI block each other. murally and mural-api together consume 95% of queue slots, yet their builds run on separate pipelines with no shared resources.

The effort bundling feature — the reason these repos share a queue — is used in only ~14% of events and adds operational complexity to shipit.

## Proposed Solution

Remove the "effort" concept from shipit and give each repository its own independent merge queue.

A simulation replaying 12 months of actual shipit events through per-repo queues shows mean wait drops from **19.9 min to 3.7 min (81% reduction)**, and 76% of PRs would experience **zero wait** (queue empty on arrival). This result is robust across multiple modeling assumptions — sensitivity analysis shows improvement between 81% and 87% depending on processing time model used. See the [full analysis](merge-queue-separate-queues-analysis.md) for methodology, data, and sensitivity analysis.

| Metric | Current (Single Queue) | Proposed (Per-Repo Queues) | Improvement |
|---|:---:|:---:|:---:|
| Mean queue wait | 19.9 min | 3.7 min | 81% |
| Median queue wait | 3.7 min | 0.0 min | 100% |
| P95 queue wait | 50.9 min | 15.7 min | 69% |
| Total wait saved / year | — | ~1,700 hours | — |

Cross-repo changes (currently ~148% of events) would require two separate shipit invocations instead of one bundled effort. This adds 2.3% more total queue entries — negligible given the wait time savings.

## Implementation

Changes are scoped to the shipit service and its configuration:

1. **shipit queue configuration**: Create per-repo queue definitions instead of the single `mural-web` queue. Each of the ~15 repos gets its own queue instance.
2. **Remove effort bundling logic**: Remove the code paths that detect cross-repo branches, link PRs into efforts, and process them as atomic units.
3. **UI changes**: In Platform Dashboard, allow viewing each queue.
4. **Developer workflow change**: For cross-repo changes, developers ship each repo's PR independently. Note that efforts today only coordinate *merging*, not deployment — there are no deploy-time ordering guarantees, so removing efforts does not change deployment behavior.

No changes are required to CI pipelines, GitHub Actions workflows, or application code.

## Alternatives Considered

Alternatives are limited because the fundamental bottleneck is the shared queue. As long as repos with independent CI pipelines share a queue, they serialize each other. Any solution that keeps the shared queue can only reshuffle *who waits*, not eliminate waiting.

**Parallel processing within the single queue.** Process N items concurrently while keeping one shared queue (similar to GitHub's native merge queue). This adds parallelism but still couples repos — a murally failure can force rollback of a concurrent mural-api build. It also introduces speculative CI complexity (builds against optimistic base branches that may not land).

**Priority lanes within the single queue.** We already have this via `shipnow`, which jumps the queue for urgent items. This helps worst-case waits for individual PRs but doesn't reduce average wait or total wait time — it redistributes wait from prioritized items to everything behind them.

**Keep efforts but make them opt-in only.** Reducing effort usage would lower the frequency of multi-repo queue items but doesn't address the core serialization. Even without efforts, repos still block each other in a shared queue.

**Do nothing.** Acceptable if 20-minute average waits and 80+ minute peak waits are tolerable. The data suggests they are a meaningful drag on developer velocity — 1,700 hours/year of wait time across the team.

## Appendix and External References

- [Full analysis: methodology, data, sensitivity analysis, and reproducibility](merge-queue-separate-queues-analysis.md)
- Analysis scripts and raw data: `merge-queue-analysis/` directory in `willik-notes`
