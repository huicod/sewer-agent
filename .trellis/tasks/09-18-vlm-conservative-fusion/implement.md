# Implementation checklist

## Task 1 — versioned conservative fusion

- [x] Add a failing test proving the default policy retains a low-score clear
  reject/revise and keeps a clear addition out of predictions.
- [x] Add explicit conservative and legacy policy constants and route
  `fuse_guided` through the selected policy.
- [x] Keep the old behavior available only through the legacy policy.
- [x] Update compatibility exports and policy pins.
- [x] Run focused fusion tests.

## Task 2 — online and cache compatibility

- [x] Update online guided tests so retained disagreements and additions are
  `needs_review`.
- [x] Make incremental cache loading select the policy from its saved version.
- [x] Preserve rejection of unknown policy versions.
- [x] Run workflow and incremental tests.

## Task 3 — offline replay evidence

- [x] Add a no-API replay command or small runner that reads the saved
  2026-09-18 guided records and applies the conservative policy.
- [x] Save a policy-pinned JSON summary and per-class comparison against the
  raw detector and legacy fusion.
- [x] Verify the replay uses zero provider attempts and inspect the diff.

## Task 4 — verification and handoff

- [x] Run the relevant backend suite and static checks available in the repo.
- [x] Review `jj diff`, report changed files and test evidence, and leave the
  training weights and provider configuration untouched.
