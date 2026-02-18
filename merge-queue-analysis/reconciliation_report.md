# Effort Rate Reconciliation Report

## Overview

This report cross-references two independent measurements of cross-repo effort rates:
- **Shipit comment parsing**: efforts detected from enqueued comment PR links
- **Branch-name matching**: same branch in murally and mural-api within 3-day window

Date range restricted to common overlap: 2025-02-18 to 2026-02-03

## Summary Counts

| Category | Count |
|----------|------:|
| Shipit effort pairs (murally+mural-api) | 351 |
| Branch-matched pairs | 420 |
| **Overlap** (in both) | 318 |
| Branch match but NOT effort | 102 |
| Effort but NOT branch match | 33 |

## Venn Diagram (text)

```
  Branch matches only: 102
         Overlap:      318
  Efforts only:        33
```

## Why Branch Matches Exceed Efforts

Of the 420 branch-matched pairs, only 318 (75.7%) were actually shipped as efforts. The remaining 102 pairs had matching branch names but were shipped independently (separate shipit invocations) or one/both PRs were never shipped via the merge queue.

This is the primary driver of the rate discrepancy: branch-name matching is a **structural** signal (developer intent to do cross-repo work), while shipit effort linking is a **behavioral** signal (developer chose to bundle PRs).

## Why Some Efforts Don't Match by Branch

Of the 351 shipit effort pairs, 33 did not appear in branch-matched pairs. Reasons:

| Reason | Count |
|--------|------:|
| Different branches | 0 |
| Same branch outside window or missing | 33 |
| Pr not in branch data | 0 |

- **Different branches**: The murally and mural-api PRs had different branch names but were still bundled as an effort (e.g., feature work split across repos with different naming).
- **Same branch outside window or missing**: Same branch name, but the PRs fell outside the 3-day creation window used by the branch analysis, or the PR was not in the branch analysis date range.
- **PR not in branch data**: The PR existed in shipit data but was outside the branch analysis dataset (different date range or not fetched).

## Sample: Branch Matches That Weren't Efforts

| Branch | murally PR | mural-api PR | Delta (days) |
|--------|-----------|-------------|:------------:|
| `add/connections-system` | #46339 | #15007 | 1 |
| `add/coresignal-for-profile-poc` | #44426 | #13909 | 1 |
| `add/cwi-1841` | #44080 | #13719 | 0 |
| `add/cwi-1857` | #44306 | #13851 | 1 |
| `add/cwi-1878` | #44286 | #13847 | 1 |
| `add/default-permissions` | #43334 | #13300 | 1 |
| `add/ecom-141-payment-element` | #44640 | #14041 | 2 |
| `add/ecomm-148-test-env` | #45693 | #14655 | 0 |
| `add/ecomm-184-update-billing-checkout` | #44823 | #14111 | 0 |
| `add/en-3200-company-ws-admins-sort` | #43899 | #13615 | 0 |
| `add/esc-1846-add-company-deletion-ui-v2` | #46527 | #15077 | 1 |
| `add/iam-1852-authd-visitor` | #43855 | #13591 | 1 |
| `add/iam-1953-char-limit` | #44105 | #13736 | 1 |
| `add/iam-2060` | #44497 | #13934 | 0 |
| `add/iam-2311-quick-filters` | #46732 | #15158 | 1 |
| `add/ipa-273` | #45667 | #14644 | 1 |
| `add/ipa-361` | #46826 | #15215 | 1 |
| `add/jan-ia-ui-testing` | #46660 | #15130 | 1 |
| `add/jan-ia-ui-testing-control` | #46690 | #15136 | 1 |
| `add/m4s-company-toggle` | #46244 | #14957 | 1 |
| ... | *82 more* | | |

## Sample: Efforts That Weren't Branch Matches

| murally PR | mural-api PR | murally branch | api branch | Same? |
|-----------|-------------|---------------|-----------|:-----:|
| #42974 | #13082 | `add/cwi-1644` | `add/cwi-1644` | Yes |
| #43005 | #13142 | `fix/scqm-281-planer-selection-resize` | `fix/scqm-281-planer-selection-resize` | Yes |
| #43284 | #13211 | `add/company-admin-elastic` | `add/company-admin-elastic` | Yes |
| #43420 | #13424 | `refactor/template-dashboard-page` | `refactor/template-dashboard-page` | Yes |
| #43498 | #13396 | `add/consolidate-see-all-endpoints` | `add/consolidate-see-all-endpoints` | Yes |
| #43511 | #13273 | `update/member-search-sort` | `update/member-search-sort` | Yes |
| #43624 | #13438 | `add/engage-873` | `add/engage-873` | Yes |
| #43806 | #13559 | `beta-fix/iam-1920` | `beta-fix/iam-1920` | Yes |
| #43968 | #13640 | `refactor/optimizely-user` | `refactor/optimizely-user` | Yes |
| #44015 | #13658 | `add/person-widget` | `add/person-widget` | Yes |
| #44032 | #13660 | `remove/chat-feature` | `remove/chat-feature` | Yes |
| #44460 | #13974 | `add/video-sidebar-in-modal` | `add/video-sidebar-in-modal` | Yes |
| #44479 | #13967 | `add/research-company-agent` | `add/research-company-agent` | Yes |
| #44484 | #13943 | `add/suggest-title-for-all-text-widgets` | `add/suggest-title-for-all-text-widgets` | Yes |
| #44537 | #13992 | `fix/max-session-signin-warn` | `fix/max-session-signin-warn` | Yes |
| #44560 | #14018 | `play-454` | `play-454` | Yes |
| #44581 | #14045 | `add/profile-widget-polish` | `add/profile-widget-polish` | Yes |
| #44597 | #14039 | `add/can-7525` | `add/can-7525` | Yes |
| #44614 | #14053 | `add/profile-widget-url` | `add/profile-widget-url` | Yes |
| #44630 | #14058 | `add/preview-mode-to-diagramming` | `add/preview-mode-to-diagramming` | Yes |
| ... | *13 more* | | | |

## Impact on Queue Separation Analysis

The discrepancy does not materially change the queue separation recommendation.

- The shipit-based analysis found **8.0%** of queue events are cross-repo efforts, producing a **2.3%** increase in queue entries under the proposed model.
- Branch matching found 420 pairs, but 102 of those were shipped independently and would NOT create paired queue entries.
- Only the 318 truly bundled efforts matter for queue modeling.
- Even doubling the effort count would add ~4-5% more entries — still negligible compared to the **94.7% wait time reduction** from queue separation.
