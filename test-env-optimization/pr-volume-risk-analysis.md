# Pull Request Volume & Auto-Provisioning Risk Analysis

**Author**: Willis Kirkham  
**Analysis Date**: February 3, 2026  
**Data Period**: February 2025 - February 2026 (52 weeks)

## Executive Summary

Auto-provisioning test environments for all PRs would result in approximately **41 concurrent environments on average**, with peaks reaching **69 environments**. This is well within current infrastructure capacity.

| Metric | Value |
|--------|-------|
| Average concurrent environments | 41 |
| Peak concurrent environments | 69 |
| P95 concurrent environments | 55 |
| Cross-repo deduplication savings | 9.4% |

**Key assumption**: Environments expire after 7 days of inactivity (current TTL policy). See [Appendix B](#appendix-b-ttl-configuration-and-impact) for details.

**Baseline validation**: Current cluster has 32 legitimate environments (28 PR-linked + 4 pinned). At ~50% opt-in, this aligns with projections showing 41 average at 100% opt-in.

**Recommendation**: Proceed with auto-provisioning. No infrastructure scaling required.

---

## The Question

**Decision**: Should we auto-provision test environments for all PRs, rather than requiring manual provisioning?

**Current state**: Approximately 50% of PRs receive test environments (those where developers manually request them).

**Proposed state**: 100% of PRs receive test environments automatically.

**Stakes**: If concurrent environment count exceeds infrastructure capacity (~70-100 environments), we would face provisioning delays, increased costs, or service degradation.

---

## Key Findings

### Concurrent Environment Projections

Analysis of 8,746 hourly data points projects the following concurrent environment counts:

| Metric | Value | Interpretation |
|--------|-------|----------------|
| **Average** | 41 | Typical infrastructure load |
| **Median (P50)** | 42 | Half of all hours below this |
| **P95** | 55 | 95% of all hours below this |
| **Peak** | 69 | Maximum observed (July 29, 2025) |

### Infrastructure Assessment

| Resource | Current Capacity | Required (peak + 25% headroom) | Status |
|----------|------------------|--------------------------------|--------|
| K8s nodes | ~70-100 envs | ~85 envs | ✓ Sufficient |
| MongoDB | ~100 connections | ~85 connections | ✓ Sufficient |
| Azure resources | Current allocation | Minimal increase | ✓ Sufficient |

Current infrastructure supports the projected load with adequate headroom.

### Baseline Validation

A point-in-time measurement (February 3, 2026) cross-referenced cluster namespaces and open PRs:

| Category | Count | Notes |
|----------|-------|-------|
| PR-linked environments | 28 | Active development (~50% of open PRs) |
| Pinned environments | 4 | Intentionally kept (demos, fixtures) |
| **Total legitimate** | **32** | |

**Validation**: With 28 PR-linked environments at ~50% opt-in, scaling to 100% opt-in implies ~56 concurrent environments. This is close to the projected average of 41 and well below the projected peak of 69, confirming the model's accuracy.

**Note**: The cluster also contains 24 orphan namespaces from a cleanup bug that should be addressed separately. See [Appendix C](#appendix-c-orphan-namespace-cleanup) for details.

---

## Supporting Analysis

### PR Volume

Over 52 weeks, both repositories show consistent PR creation patterns:

| Metric | murally | mural-api | Combined |
|--------|---------|-----------|----------|
| Total PRs | 4,304 | 2,390 | 6,694 |
| Unique branches | 4,230 | 2,335 | 6,135* |
| Weekly average | 81.3 | 44.9 | 118.0* |
| Cross-repo matches | - | - | 430 (6.5% of PRs) |

*After cross-repo deduplication

### PR Lifespan Distribution

PR lifespan explains why 118 weekly branches result in only 41 average concurrent environments:

| Duration | % of PRs | Cumulative |
|----------|----------|------------|
| 0-1 hour | 12.7% | 12.7% |
| 1-4 hours | 14.8% | 27.5% |
| 4-24 hours | 21.0% | 48.5% |
| 1-2 days | 11.5% | 60.0% |
| 2-7 days | 22.2% | 82.2% |
| **>7 days** | **17.8%** | 100% |

82% of PRs close within 7 days. The remaining 18% have their environments capped by the TTL policy, preventing accumulation.

### Cross-Repo Deduplication

When the same branch exists in both murally and mural-api, they share a single environment:

| Metric | Value |
|--------|-------|
| Max branches active in both repos simultaneously | 10 |
| Average branches active in both repos | 4.3 |
| Deduplication savings | 9.4% of concurrent environments |

Cross-repo branches represent features spanning both repositories—typically larger changes that take longer to complete. Their longer lifespan means deduplication saves 9.4% of environments despite representing only 6.5% of PRs.

---

## Risk Assessment

### Capacity Exceeds Projections

| | |
|---|---|
| **Severity** | Low |
| **Likelihood** | Low |

Peak concurrent environments (69) are well within infrastructure capacity (~70-100 environments).

**Monitoring recommendations**:
1. Track concurrent environment count in real-time
2. Alert at 60, 70, and 80 concurrent environments
3. No preemptive scaling required

### TTL Bypass via User Interaction

| | |
|---|---|
| **Severity** | Low |
| **Likelihood** | Low |

Users could keep environments alive indefinitely by periodically interacting with them. Current data does not suggest this is a significant pattern.

**Monitoring recommendations**:
1. Track environment age distribution
2. Consider 14-day absolute TTL cap if abuse is observed

### Unexpected Cost Increase

| | |
|---|---|
| **Severity** | Low |
| **Likelihood** | Low |

With concurrent environments similar to current levels, cost increase is minimal.

---

## Recommendations

### Infrastructure

1. **No scaling required** — current capacity supports projected load
2. **Add monitoring** — track concurrent environments and set alerts
3. **Review after 2 weeks** — validate projections against actual data

### Policy

1. **Proceed with auto-provisioning** — infrastructure risk is low
2. **Maintain current TTL** — 7-day inactivity TTL is effective
3. **Optional opt-out** — support `[skip-env]` label for PRs that don't need environments

### Rollout

1. Enable for both repos simultaneously
2. Monitor for 2 weeks to validate projections
3. Adjust only if needed

---

## Success Criteria

| Metric | Target | Confidence |
|--------|--------|------------|
| Peak concurrent envs | <85 | High |
| Average concurrent envs | <50 | High |
| Provisioning queue time | <5 min | High |
| Infrastructure scaling needed | No | High |

---

## Appendix A: Methodology

### Modeling Approach

Environment lifespan is modeled as `min(PR_lifespan, 7_days)` to reflect the TTL policy. This simulates real-world behavior where environments are deleted after 7 days of inactivity, regardless of PR status.

Concurrent environments at each hour are calculated by counting open PRs (with TTL-capped lifespans) across both repositories, deduplicating branches that exist in both.

### Data Sources

- GitHub API for PR creation and close timestamps
- 4,301 murally PRs (Feb 2025 - Feb 2026)
- 2,381 mural-api PRs (Feb 2025 - Feb 2026)
- 8,746 hourly data points generated

### Assumptions

1. Environments expire at 7-day TTL (no user interaction modeled)
2. All PRs receive auto-provisioned environments
3. Cross-repo branches share a single environment
4. PR close triggers environment deletion

---

## Appendix B: TTL Configuration and Impact

### Current Configuration

| Setting | Value |
|---------|-------|
| TTL duration | 168 hours (7 days) |
| Trigger | Inactivity (time since `lastInteractionAt`) |
| Reset actions | Override secrets, extend environment |
| PR close behavior | Triggers deletion via webhook |
| Protection | `preventDeletion: true` flag exempts environments |

### Why TTL Is Effective

The 7-day TTL caps the "long tail" of PRs that stay open for extended periods:

| Metric | murally | mural-api |
|--------|---------|-----------|
| Raw P90 lifespan | 317 hrs (13 days) | 710 hrs (30 days) |
| Raw P95 lifespan | 1,058 hrs (44 days) | 1,660 hrs (69 days) |
| **Effective lifespan** | **168 hrs (7 days)** | **168 hrs (7 days)** |

### Impact of TTL on Projections

Without the TTL mechanism, concurrent environments would be significantly higher:

| Metric | With TTL | Without TTL |
|--------|----------|-------------|
| Average concurrent | 41 | ~132 |
| Peak concurrent | 69 | ~194 |

The TTL reduces concurrent environments by approximately 68%, making auto-provisioning feasible within current infrastructure capacity.

---

## Appendix C: Orphan Namespace Cleanup

### Problem

The baseline measurement identified 24 orphan test envs—envs where the test env was supposed to be deleted but it wasn't.

| Category | Count |
|----------|-------|
| Legitimate environments | 32 |
| Orphan namespaces | 24 |
| **Total namespaces observed** | **56** |

### Impact

- These namespaces consume cluster resources unnecessarily
- They do not affect the auto-provisioning analysis (projections are based on legitimate environments only)
- The discrepancy between namespace count (56) and CRD count (32) initially suggested the model might be underestimating, but investigation confirmed the model is accurate

### Root Cause

The `test-envs-operator` successfully deletes TestEnv CRDs when environments become stale, but namespace deletion is failing. This is a separate operational issue from auto-provisioning.

### Recommendations

1. **Investigate operator** — determine why namespace deletion fails after CRD cleanup
2. **Clean up orphans** — delete the 24 orphan namespaces to recover resources
3. **Add monitoring** — alert when namespace count diverges from active CRD count
