# Backend unit report

## RED

- Initial focused run failed collection on the absent human-review contract/policy/storage
  modules; the HTTP run then failed 3 tests because `create_app` had no review starter.
- A later starter race test failed with `review_not_waiting` when an identical audit became
  visible between the initial cache read and terminal-state check.
- Exact compact-receipt regression initially accepted an extra evidence field; it now
  rejects any receipt whose keys differ from the four-field contract.

## GREEN

- Added the approved contracts, pure batch policy, atomic PostgreSQL audit repository,
  compact-receipt Temporal starter, GET/POST router, app wiring, and storage initializer.
- Focused unit/API plus opt-in isolated PostgreSQL run: 22 passed. Scoped Ruff: clean.
- Earlier full backend run during integration: 548 passed, 78 skipped. Root owns the final
  combined rerun because workflow/worker files are changing concurrently.

## Result

Interfaces match `backend-brief.md`, including the 1,000,000-byte compact JSON bound and
post-update persisted-record reload. No backend-unit concern remains. The last combined run
had only concurrently owned worker/bootstrap compatibility failures; root is updating them.
