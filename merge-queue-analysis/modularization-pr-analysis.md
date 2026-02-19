# Modularization PR Analysis: Impact on Merge Queue Findings

**Author**: Willis Kirkham
**Analysis Date**: February 18, 2026
**Data Period**: February 2025 – February 2026 (12 months)

## Summary

A reviewer raised the concern that modularization PRs — murally-only structural reorganization work — could be inflating murally's queue share and making cross-repo efforts appear less common than they are.

**Finding: Even under the most aggressive worst-case assumptions, modularization PRs do not materially affect the analysis conclusions.**

| Metric | Original | Worst-case exclusion | Delta |
|--------|:--------:|:--------------------:|:-----:|
| Total queue events | 5,204 | 4,709 | -495 |
| murally share | 69.8% | 66.6% | -3.2pp |
| mural-api share | 25.4% | 28.0% | +2.6pp |
| Cross-repo effort % | 8.0% | 8.8% | +0.8pp |

Even if every possible modularization-related PR is excluded, the effort rate moves from 8.0% to 8.8% — a +0.8pp change, less than 1 percentage point and smaller than the uncertainty introduced by the worst-case assumption itself (which overstates modularization by roughly 2x based on manual validation). This does not alter any conclusions from the [merge queue separation analysis](merge-queue-separate-queues-analysis.md).

---

## Methodology

### Overview

We fetched all **3,472** merged PRs from `tactivos/murally` (Feb 18, 2025 – Feb 18, 2026) via GitHub GraphQL API, collecting title, branch name, body, and change stats. A deterministic classifier bucketed each PR as clear-YES, clear-NO, or ambiguous. Rather than classify each ambiguous PR individually, we assumed the worst case: all ambiguous PRs are modularization. We then validated this approach by manually reviewing the most recent 1,000 PRs to confirm the classifier's accuracy.

### Deterministic classification rules

**Clear YES — definitely modularization (110 PRs).** A PR was classified as modularization if any of these matched:

- Branch starts with `move/`, `modularize-`, `create/package-`, or `extract/`
- Title contains modularization keywords: "modulariz\*", "move to package", "extract to package", "split package", "reorganize package", "migrate to package", "create package", or "new package" (case-insensitive substring matches)
- Body contains modularization keywords AND either the body or title references `packages/` (this conjunction prevents false positives from PRs that casually mention modularization without actually doing cross-package work)

**Clear NO — definitely not modularization (3,055 PRs).** A PR was classified as clear-NO if it matched any of the following rules AND did not already match a YES rule:

1. **Jira feature ticket with no modularization signal**: Title or branch contains a known Jira project prefix (`CAN-`, `CWI-`, `ECOMM-`, `ENGAGE-`, `PLAY-`, `IAM-`, `ESC-`, `BC-`, `SCQM-`, `COLLAB-`, `AI-`, `AUTH-`, `PLAT-`, `INFRA-`, `SRE-`, `DATA-`, `DX-`) AND the PR body has no modularization keywords AND the branch doesn't match ambiguous patterns (`next-*`, `migrate-*`).

2. **Small PR with no modularization keywords**: `changedFiles ≤ 3` AND no modularization keywords anywhere in title, body, or branch.

3. **Standard non-modularization branch prefix**: Branch starts with `fix/`, `hotfix/`, `bugfix/`, `chore/`, `docs/`, `ci/`, `test/`, `revert/`, `release/`, `bump/`, or `dependabot/` AND doesn't match ambiguous patterns AND no modularization keywords in title or body.

4. **`update/` branch with no modularization signal**: Starts with `update/` AND doesn't match `update/next-*` or `update/migrate-*` AND no modularization keywords in title or body.

5. **`add/` branch with no modularization signal**: Starts with `add/` AND doesn't match `add/next-*` AND no modularization keywords in title or body.

6. **`remove/` branch with no modularization signal**: Starts with `remove/` AND no modularization keywords in title or body.

Every NO rule requires that no modularization keywords appear in the PR body. A Jira feature PR that mentions "modularization" in its description falls through to ambiguous rather than being classified as clear-NO.

**Ambiguous — everything else (307 PRs).** Any PR matching neither YES nor NO rules. In practice: `fix/next-*` branches (the NEXT project overlaps with modularization), `migrate-*` branches (could be dependency or structural), `feat/*` branches without Jira prefixes, bare branch names, and other patterns not covered by the NO rules.

### Worst-case approach

Rather than classify each ambiguous PR individually, we computed the **worst case**: assume ALL 307 ambiguous PRs are modularization. This gives an upper bound of **417 modularization PRs** (110 clear + 307 ambiguous), mapping to **495 queue events** when cross-referenced against the [shipit queue data](queue_events.csv).

This deliberately overstates modularization to show that even the maximum conceivable exclusion doesn't move the needle.

---

## Validation

To confirm the classifier's accuracy, we manually reviewed the most recent 1,000 PRs (95 YES, 760 NO, 145 ambiguous), examining title, branch name, body text, and changed-file counts for each. This sample covers roughly 29% of the 12-month analysis period; modularization patterns could differ in earlier months, though the worst-case calculation uses the full dataset and is unaffected by any temporal variation in the validation sample.

### YES bucket: ~98% accurate

Every YES-classified PR reviewed was genuine modularization — moving files between packages, creating new packages, or updating cross-package imports. Representative examples:

- `move/room-route-package` — "Move RoomRoute to its own package" (16 files)
- `modularize-src-ui-2` — "Phase 2: Migrate UI components from src/ui to @muralco/legacy-ui" (93 files)
- `share-modal-package` — "Migrates Share Modal to @muralco/share-modal" (166 files)
- `move/sdk-api-priority` — "Move SDK API files with 3+ imports" (143 files)

Two borderline cases were modularization *tooling* rather than modularization itself (#46978 `code-package-analysis`, #47027 `move-to-package-package-imports`). Whether these count is a judgment call; including them errs on the side of overstating modularization, which aligns with the worst-case framing.

### NO bucket: ~99%+ accurate — no false negatives found

We found zero cases where actual "move code between packages" work was misclassified as clear-NO. The body-keyword guard is the key safeguard: any PR that mentions modularization in its description cannot be classified as NO.

Several NO-bucket PRs have the word "Move" in their titles but are correctly classified because the movement described is behavioral, not structural:

- `add/can-8367` — "[CAN-8367] Move zoom-to-selection tool to the navigation zoom menu" (UI element placement)
- `fix/can-8337` — "[CAN-8337] Move stageManager global variable into CET SDK instance" (refactoring within a single module)
- `fix/ordering-resizing` — "[CAN-8276] Move widgets to top after dropping and absorbing" (runtime behavior)

One near-miss: #47129 (`update/mural-limit-modal-pattern`) has `[NEXT-448]` in its title, but the branch doesn't match ambiguous patterns and the body doesn't contain modularization keywords. The PR moves code to a new in-package pattern — it's part of the NEXT project but restructures within a package, not between packages. This is a reasonable NO classification.

### Ambiguous bucket: intentionally broad

The ambiguous bucket is larger than necessary because several branch prefixes aren't handled by the NO rules (`feat/`, `bug/`, `refine/`, `refactor/`, `feature/`, bare branch names). Of the 145 ambiguous PRs in the recent 1,000:

- **~60% are clearly modularization**: `fix/next-*` branches with titles like "Move X SDK into packages", `update/migrate-*` branches migrating code to `@muralco/canvas`. These *should* be YES but the `fix/next-*` branch prefix routes them to ambiguous instead.
- **~25% are clearly NOT modularization**: feature work on `feat/en-*` branches, bug fixes on `bug/*` branches, infrastructure changes, test configuration, backmerge conflict resolutions.
- **~15% are genuinely ambiguous**: import cleanup, package dependency hygiene, refactoring that may or may not be modularization-related.

This over-breadth in the ambiguous bucket means our worst-case assumption (all ambiguous = modularization) is *more conservative than necessary*. The true modularization count is likely **150–200 PRs**, well below the 417 upper bound.

### Validation limitations

The validation was performed by the same analyst who wrote the classifier, which introduces a risk of confirmation bias. To mitigate this, the `last_1000_for_review.json` export (generated by `validate_classification.py`) is available for independent spot-checking. We encourage the reviewer who raised the original concern to sample PRs from each bucket and verify the classifications.

---

## Results

### Worst-case queue statistics

| Metric | Original | Excl. modularization (worst case) |
|--------|:--------:|:---------------------------------:|
| Total queue events | 5,204 | 4,709 |
| murally events | 3,630 | 3,135 |
| murally share | 69.8% | 66.6% |
| mural-api events | 1,320 | 1,320 |
| mural-api share | 25.4% | 28.0% |
| Cross-repo effort events | 415 | 415 |
| Cross-repo effort % | 8.0% | 8.8% |

Cross-repo effort events are unchanged because modularization is by definition murally-only structural work and does not appear in cross-repo efforts.

### Classification breakdown

| Category | PRs | % of total |
|----------|:---:|:----------:|
| Clear modularization | 110 | 3.2% |
| Ambiguous (upper bound) | 307 | 8.8% |
| **Worst-case total** | **417** | **12.0%** |
| Clear not-modularization | 3,055 | 88.0% |
| **All merged PRs** | **3,472** | **100%** |

### Why this doesn't change the analysis

1. **Effort rate barely moves**: 8.0% → 8.8% is a +0.8pp change — less than 1 percentage point, and smaller than the uncertainty in the worst-case assumption itself. Cross-repo efforts remain a small minority of queue volume regardless.

2. **murally still dominates**: Even excluding every possible modularization PR, murally accounts for 66.6% of queue events — still far more than mural-api's 28.0%. The case for queue separation is driven by this volume imbalance, which persists.

3. **Wait time improvement is unaffected**: The queue separation simulation showed 81.3% mean wait time reduction. Removing 495 events from murally's queue would, if anything, *reduce* the benefit of separation marginally — but 66.6% vs 28.0% still creates enormous blocking in a single queue.

4. **The 417 number overstates reality**: Manual validation shows roughly a quarter of the ambiguous bucket is clearly not modularization. The true count is likely 150–200 PRs, making the real impact even smaller than the worst case reported here.

5. **Classifier gaps are absorbed by the worst case**: Even if the YES rules miss modularization PRs that use different terminology or involve very small file moves, those PRs would land in the ambiguous bucket (not the NO bucket, which requires the absence of modularization signals). Since all ambiguous PRs are already counted as modularization in the worst case, classifier under-detection does not affect the upper bound.

---

## Conclusion

Modularization PRs are real — roughly 110 PRs (3.2% of murally merges) are clearly modularization work, with an upper bound of 417 (12.0%) if every ambiguous PR is generously included. However, **this does not materially affect the merge queue analysis findings**:

- The effort rate changes by less than 1 percentage point
- murally still accounts for two-thirds of queue volume
- The case for queue separation remains strong regardless of modularization exclusion

The classifier was validated against the most recent 1,000 PRs with no false negatives found in the NO bucket and ~98% accuracy in the YES bucket. The ambiguous bucket intentionally errs broad, and our worst-case framing absorbs all of that uncertainty. The reviewer's concern is reasonable but the data shows it is a non-factor for the decision at hand.

---

## Reproducibility

All scripts and cached data are in the `merge-queue-analysis/` directory. To reproduce:

```bash
# Prerequisites: Python 3.10+, gh CLI authenticated, virtualenv
cd merge-queue-analysis
python -m venv venv
source venv/bin/activate
pip install requests

# Phase 1-4: Fetch PR metadata, classify, fetch files for ambiguous PRs
# (uses cached data in murally_prs_metadata.json if present; --refresh to re-fetch)
python classify_modularization_prs.py fetch

# Compute worst-case queue impact statistics
python worst_case_check.py

# Export last 1000 PRs with bucket labels for manual validation
python validate_classification.py

# Inspect specific buckets interactively
python review_buckets.py yes
python review_buckets.py ambiguous
python review_buckets.py no --limit 200
```

### Scripts

| Script | Purpose |
|--------|---------|
| `classify_modularization_prs.py` | Main classifier: fetches PRs via GitHub GraphQL/REST, applies deterministic rules, outputs ambiguous PRs for review |
| `worst_case_check.py` | Computes queue statistics assuming all ambiguous PRs are modularization |
| `validate_classification.py` | Exports recent PRs with bucket labels for manual validation |
| `review_buckets.py` | Prints compact per-bucket PR listings for interactive review |

### Data files

| File | Contents |
|------|----------|
| `murally_prs_metadata.json` | Cached metadata for all 3,472 merged PRs (title, branch, body, stats) |
| `murally_pr_files_cache.json` | Cached file lists for ambiguous + validation-sample PRs |
| `queue_events.csv` | Shipit queue event data (input from merge queue analysis) |
| `ambiguous_prs_for_review.json` | 307 ambiguous PRs formatted for review |
| `last_{N}_for_review.json` | Generated by `validate_classification.py` — most recent N PRs with bucket classifications |
