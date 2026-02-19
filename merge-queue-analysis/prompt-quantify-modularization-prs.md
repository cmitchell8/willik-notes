# Prompt: Quantify modularization PRs in murally over the last 12 months

## Context

We have a merge queue analysis showing that murally accounts for 70% of shipit queue volume (3,630 events out of 5,204). A reviewer raised the concern that modularization PRs — which are murally-only by nature and don't represent typical feature work — could be inflating murally's share and making cross-repo efforts (8% of events) appear less common than they actually are.

A simple search for "NEXT-*" in PR titles found 184 modularization PRs, but not all modularization work uses that Jira prefix. We need a more comprehensive count by looking at what the commits actually changed.

## Goal

Produce a count and list of PRs in `tactivos/murally` from the last 12 months (Feb 2025 – Feb 2026) that are modularization-related, based on what their commits actually touched. The output should allow us to say: "X out of Y merged PRs (Z%) were modularization work" with confidence.

## What counts as a modularization PR

Modularization PRs reorganize code between packages without changing behavior. They typically:

- **Move files between packages** (e.g., moving a component from `src/` into `packages/canvas/`)
- **Create new packages** (adding a new `packages/<name>/` directory with package.json, tsconfig, etc.)
- **Update cross-package imports** after moves (lots of import path changes across many files)
- **Split a package** into smaller packages
- **Move types/interfaces** into shared packages
- **Update package dependency graphs** (changes to multiple package.json files to rewire dependencies)

Modularization PRs do NOT include:

- Feature work scoped to 1-2 packages (even if it touches package boundaries)
- Bug fixes
- Dependency upgrades
- Test additions/changes that don't reorganize code
- Refactors that improve code within a single package without moving it

Key heuristic signals of modularization:

1. **High ratio of file renames/moves** — `git log --diff-filter=R` shows many renames
2. **Many files changed across 3+ packages** — touches `packages/A/`, `packages/B/`, `packages/C/`, etc.
3. **Lots of import path changes** — diffs dominated by `from '../old-path'` → `from '@muralco/new-package'`
4. **New package.json files created** in `packages/` directory
5. **No behavioral changes** — the diff is structural, not functional
6. **PR title or branch name** contains keywords like: modulariz, extract, move, split, reorganize, NEXT-

## Approach

### Step 1: Get all merged PRs with their commit SHAs

Use `gh` CLI to fetch all merged PRs in the date range:

```bash
gh pr list --repo tactivos/murally --state merged --limit 500 \
  --search "merged:2025-02-01..2026-02-18" \
  --json number,title,mergedAt,headRefName,commits
```

Note: `gh pr list` caps at 1000 results. The repo had ~3,635 merged PRs in this period. You'll need to paginate by date ranges (e.g., month by month) to get all of them.

Alternatively, use the GitHub GraphQL API to paginate through all merged PRs:

```bash
gh api graphql -f query='
query($cursor: String) {
  repository(owner: "tactivos", name: "murally") {
    pullRequests(
      first: 100,
      after: $cursor,
      states: [MERGED],
      orderBy: {field: CREATED_AT, direction: DESC}
    ) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        headRefName
        mergedAt
        changedFiles
        additions
        deletions
      }
    }
  }
}'
```

### Step 2: Identify candidate modularization PRs

From the PR list, flag candidates using fast heuristics:

1. **Title/branch match**: PR title or branch contains NEXT-, modulariz, extract, move-to-package, split-package, reorganize
2. **High file count**: PRs touching 50+ files are more likely to be large reorganizations
3. **Cross-package changes**: Determine how many distinct `packages/*/` directories are touched

For the candidates (and a sample of non-candidates for validation), fetch the actual diff stats:

```bash
# For each candidate PR, get the file-level diff
gh pr diff <PR_NUMBER> --repo tactivos/murally --name-only
```

### Step 3: Classify each candidate

For each candidate PR, classify it by checking:

1. **How many packages are touched?** Count distinct `packages/*/` prefixes in changed files
2. **Are there file renames across packages?** Check for files that moved from one package dir to another
3. **Are new packages created?** Look for new `packages/<name>/package.json` files
4. **What's the ratio of import-path changes to logic changes?** If the diff is mostly import rewiring, it's modularization
5. **Does the PR change behavior?** If it only restructures, it's modularization

Use an LLM to classify borderline cases by examining the diff summary.

### Step 4: Cross-reference with shipit data

We have shipit queue event data in `queue_events.csv` (5,204 events with branch names and PR numbers). Cross-reference the modularization PR list with shipit events to determine:

- How many modularization PRs went through shipit?
- What % of murally's 3,630 queue events were modularization?
- How do the analysis numbers change if modularization events are excluded?

Recompute these key metrics with modularization PRs excluded:

| Metric | All data | Excluding modularization |
|--------|----------|--------------------------|
| Total queue events | 5,204 | ? |
| murally share | 69.8% | ? |
| mural-api share | 25.4% | ? |
| Cross-repo effort % | 8.0% | ? |

### Step 5: Output

Produce a markdown document with:

1. **Summary**: "X out of Y murally PRs (Z%) were modularization work"
2. **Methodology**: How PRs were identified and classified
3. **Temporal distribution**: Monthly breakdown (are they concentrated in certain months?)
4. **Impact on analysis**: Recomputed queue statistics with modularization excluded
5. **PR list**: Table of all identified modularization PRs (number, title, merged date, files changed)
6. **Conclusion**: Whether modularization PRs materially affect the merge queue analysis findings

Save the output to: `merge-queue-analysis/modularization-pr-analysis.md`

## Important notes

- The murally repo is at `/Users/wkirkham/dev/mural/murally` (on `master` branch) — you can use `git log` locally for commit-level analysis
- The shipit queue data is at `/Users/wkirkham/dev/willik-notes/merge-queue-analysis/queue_events.csv`
- The murally repo has ~94 packages under `packages/`
- Use `gh` CLI for GitHub API access (already authenticated)
- Be conservative in classification — when in doubt, don't count it as modularization
- The goal is to address the reviewer's concern, so frame the output as a response to: "Could modularization PRs be skewing the data?"
