# Pull Request Volume & Auto-Provisioning Risk Analysis

**Author**: Willis Kirkham  
**Analysis Date**: February 3, 2026  
**Data Period**: February 2025 - February 2026 (52 weeks)

## Executive Summary

Auto-provisioning test environments for all PRs would result in approximately **41 concurrent environments on average**, with peaks reaching **69 environments**. This is within infrastructure capacity of ~89 environments, provided auto-provisioned environments are provisioned without multigeo.

| Metric | Value |
|--------|-------|
| Average concurrent environments | 41 |
| Peak concurrent environments | 69 |
| P95 concurrent environments | 55 |
| Cluster capacity (without multigeo) | ~89 |

**Key assumptions**: Environments expire after 7 days of inactivity (current TTL policy). Auto-provisioned environments do not include multigeo — multigeo nearly doubles the per-environment pod footprint and would reduce cluster capacity to ~54, below the projected peak.

**Baseline validation**: Current cluster has 32 legitimate environments (28 PR-linked + 4 pinned). At ~50% opt-in, this aligns with projections showing 41 average at 100% opt-in.

**Recommendation**: Proceed with auto-provisioning. Disable multigeo by default for auto-provisioned environments. No infrastructure scaling required.

---

## The Question

**Decision**: Should we auto-provision test environments for all PRs, rather than requiring manual provisioning?

**Current state**: Approximately 50% of PRs receive test environments (those where developers manually request them).

**Proposed state**: 100% of PRs receive test environments automatically.

**Stakes**: If concurrent environment count exceeds infrastructure capacity (~89 environments), we would face provisioning delays, increased costs, or service degradation.

---

## Key Findings

### Concurrent Environment Projections

PR open and close timestamps were analyzed for the last year, factoring in current cleanup policies of test environments. The result is 8,746 hourly data points that projects the following concurrent environment counts:

| Metric | Value | Interpretation |
|--------|-------|----------------|
| **Average** | 41 | Typical infrastructure load |
| **Median (P50)** | 42 | Half of all hours below this |
| **P95** | 55 | 95% of all hours below this |
| **Peak** | 69 | Maximum observed (July 29, 2025) |

### Infrastructure Assessment

| Resource | Current Capacity | Required (peak + 25% headroom) | Status |
|----------|------------------|--------------------------------|--------|
| K8s nodes | ~89 envs | ~85 envs | ✓ Sufficient |
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

Over 52 weeks, both repositories show these PR creation patterns:

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

Peak concurrent environments (69) are within infrastructure capacity (~89 environments).

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
3. **Optional opt-out** — support `[no-tenv]` label for PRs that don't need environments

### Rollout

1. Enable for both repos simultaneously
2. Monitor for 2 weeks to validate projections
3. Adjust only if needed

---

## Success Criteria

| Metric | Target | Confidence |
|--------|--------|------------|
| Peak concurrent envs | <89 | High |
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

## Appendix C: Orphan Test Env Cleanup

Update: Mar 2, 2026

After this analysis was done, Platform Engineering cleaned up orphan environments. A couple of causes were at play. First, long branch names resulted in provision/deprovision failures due to resource name length restrictions in Azure. Second, test-env-operator wasn't resilient to failures. If there was a failure, the custom resource (CR) for the env would still be deleted. This meant on subsequent reconciliation loops, the operator wouldn't clean up previously failed resources for that env (since there was no CR for it anymore).

### Problem

The baseline measurement identified 24 orphan test envs—envs where the test env was supposed to be deleted but it wasn't.

| Category | Count |
|----------|-------|
| Legitimate environments | 32 |
| Orphan namespaces | 24 |
| **Total namespaces observed** | **56** |

### Impact

- These namespaces consume cluster resources unnecessarily
- They do not affect the auto-provisioning analysis (projections are based on legitimate environments only), but the cleanup needs to be fixed before enabling auto-provisioning.
- The discrepancy between namespace count (56) and CRD count (32) initially suggested the model might be underestimating, but investigation confirmed the model is accurate

### Root Cause

The `test-envs-operator` successfully deletes TestEnv lifecycle documents when environments become stale, but namespace deletion is failing. This is a separate operational issue from auto-provisioning.

### Recommendations

1. **Investigate operator** — determine why namespace deletion fails after CRD cleanup
2. **Clean up orphans** — delete the 24 orphan namespaces to recover resources
3. **Add monitoring** — alert when namespace count diverges from active CRD count

---

## Appendix D: Cluster Capacity Estimate

Cluster: testing-envs-v2-aks (20 nodes, `Standard_E4as_v5`)
Analysis date: March 2, 2026
Sources: Terraform config (`terraform-mural-testing-envs/main.tf`), template definitions (`mural-test-envs-templates`), live kubectl measurements

### Summary

The cluster can support approximately 54 concurrent test environments under the default configuration, where every environment includes EU multigeo and most include banksy (~36 pods each). Pod count -- not CPU or memory -- is the binding constraint, with compute resources at roughly 2x headroom beyond the pod limit.

Multigeo is the single largest driver of per-environment cost, nearly doubling the pod footprint from ~22 to ~35 by adding EU-zone duplicates of the API, Redis, realtime, upload, and 7 worker types. Making multigeo optional and off by default would raise capacity to ~89 environments. Scaling beyond ~89 would require adding nodes or implementing other pod reduction strategies like single-worker.

### Layer 1: Pod-Based Estimate

AKS with kubenet networking caps each node at 110 pods. With 20 nodes, the cluster has 2,200 pod slots.

Infrastructure consumes 232 of those. The bulk is 9 DaemonSets (180 pods: Datadog, two ingress-nginx controllers, kube-proxy, azure-ip-masq-agent, cloud-node-manager, two CSI drivers, kured). The remaining 52 are spread across kube-system controllers, the backing namespace (operator, dashboard, mgmt, sso-proxy, smooth-operator, flaky-monitor), ArgoCD, cert-manager, and Rancher.

That leaves 1,968 slots for test environments. The templates provision multigeo by default and most environments also include banksy, yielding ~36 pods per environment. Core services alone account for ~22 pods (API, 10 workers, Elasticsearch, Redis, and supporting services); multigeo adds ~11 EU-zone duplicates, and banksy adds one more.

| Configuration | Pods/env | Capacity | Notes |
|---------------|----------|----------|-------|
| Default (multigeo + banksy) | ~36 | ~54 | Current standard |
| Multigeo without banksy | ~35 | ~56 | |
| Core-only (multigeo off) | ~22 | ~89 | Requires making multigeo optional |

### Layer 2: Resource Validation

The cluster has 77.2 allocatable CPU cores and 581 GiB memory (3,860m CPU and ~29 GiB per node). After infrastructure overhead (~14 cores, ~12 GiB), approximately 63 cores and 569 GiB remain for test environments.

Each default environment requests ~0.61 CPU cores and ~4.3 GiB memory. At those rates, CPU could support ~103 environments and memory ~132 -- both well above the pod-based limit of ~54. Pods are the bottleneck; increasing capacity requires more pod slots, not larger VMs.

### Layer 3: Live Validation (March 2, 2026)

29 test environments were running, consuming ~1,106 pods (56% of available slots). The model predicts room for ~24 more, consistent with 862 remaining slots at ~36 pods each.

26 of the 29 environments had 33-37 pods, confirming the default multigeo configuration. Three outliers (55-64 pods) were inflated by accumulated failed Job pods, not additional services. About 12% of tenv pod slots were occupied by non-running pods (failed jobs, image pull errors); cleaning these up would recover 1-2 additional environment slots.

### Caveats

- Pod fragmentation means pods can't split across nodes, so real capacity is somewhat below the theoretical maximum.
- This analysis uses Kubernetes resource *requests* (scheduling basis), not runtime consumption.
- All measurements are point-in-time (March 2, 2026) and will drift as cluster services change.
