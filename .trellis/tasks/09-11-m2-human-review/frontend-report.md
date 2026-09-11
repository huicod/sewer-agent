# T09 frontend report

## RED

- Baseline before edits: 5 files / 32 tests passed.
- New review/video tests initially failed because `ReviewPanel`, `VideoTimeline`, and the
  review/video API functions did not exist.
- TaskCard integration tests failed until review/history entry points and lazy timeline
  mounting were wired in.
- Focused regressions failed before fixes for backend-valid named `OTHER` confirmation,
  timezone-less audit timestamps, stale timeline state after a task switch, and evidence
  frame previews sourced by `frame_index`.
- Combined review found and reproduced two policy edges before correction: zero-finding
  audits lacked an original-input link, and unlabeled findings incorrectly offered confirm.
- Integration review reproduced same-task stale state: unchanged polling had to preserve an
  in-progress draft, while a changed snapshot revision/status had to reload review truth;
  an expanded timeline also had to refresh after a later snapshot gained video evidence.

## GREEN

- Added exact review/video TypeScript projections and HTTP-boundary validation.
- Added complete-batch review UI with native SVG evidence coordinates, explicit actions and
  reasons, stable identical-retry IDs, refreshed accepted audit, original-input access, and
  stale-task guards. Unlabeled findings require relabel/reject instead of a predictable 409.
- Added lazy video discovery/timeline with persisted intervals, decode state, source link,
  and evidence-frame links/previews matched only from saved frame assets.
- Integrated focused controls into the existing TaskCard; no review GET occurs before the
  user opens the panel. Legacy terminal records with `record: null` remain readable.
- TaskCard now supplies snapshot revision/status to both detail views. Review reloads only
  on a meaningful projection change, while expanded timelines refresh in place on revision
  changes and expose an explicit retry; collapsed timelines remain lazy. Late responses are
  ignored after projection changes, task switches, or unmounts.

## Verification

- `PATH="$PWD/.tools/node/bin:$PATH" npm --prefix frontend test -- --run`
  - 8 test files passed; 52 tests passed.
- `PATH="$PWD/.tools/node/bin:$PATH" npm --prefix frontend run build`
  - TypeScript check and Vite production build passed; 20 modules transformed.

No dependency, backend, service, model/API, VCS, or public-server changes were made by this
frontend unit. Browser visual acceptance was not run; DOM behavior, HTTP contracts, tests,
and production compilation were verified.
