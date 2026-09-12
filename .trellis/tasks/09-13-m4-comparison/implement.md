# T14-A Implementation Plan

Trellis controls lifecycle; use executing-plans/TDD with repo overrides (existing jj, approved roadmap, one review). No extra ledger.
Goal: selected saved evidence→durable comparison→dialogue/UI/export, no inspection mutation.
Architecture/typed interfaces and owners: design.md. Stack existing Python/FastAPI/PostgreSQL/Temporal/React, no packages.

- [x] Domain test-first: reference AJ confirmed1/current AJconfirmed2 + rejected1 => confirmed_delta1; retain all statuses/evidence, validate mismatched counts/source/IDs. Implement contracts/build function. Run `.venv/bin/pytest -q backend/tests/test_comparison.py`.
- [x] Storage test-first real unique PG schema: two scoped terminal tasks+media; save comparison returns immutable rows/snapshots, concurrent/retry winner same; changed task records do not overwrite; foreign/pending/same/mismatchedmedia rejected, no original changes. Implement storage helper/forwarder, run `SEWER_RUN_STORAGE_TEST=1 .venv/bin/pytest -q backend/tests/integration/test_comparison_storage.py`.
- [x] Root test-first intent/turn/preflight: compare([compare]),reference field optional old load, missing/guessed/bodyconflict/newupload no execution, noncompare reference no silent tool, current/session pin; dispatch uses saved result and actual attempt. Run `.venv/bin/pytest -q backend/tests/test_comparison_conversation.py`.
- [x] Presentation test-first HTTP/HTML/source escapes and React HTTP fixtures: compare/table/links, absent/error/stale states, selector retry binds reference; run scoped pytest, frontend tests and build. Frozen schemas in design.md.
- [x] Root bounded real PG+Temporal dialogue compare with deterministic intent double to inject lost dispatch completion; cache before source reread after worker replacement/retry, no YOLO/VLM/GPU calls. One real API intent smoke, no extra accuracy runs; persisted evidence under var/evaluations/2026-09-13-comparison/live-smoke.
- [x] One independent scoped review plus fixes, final backend pytest/Ruff/frontend tests/build/diff, concise report/roadmap/spec/journal and jj checkpoint. T14-B standards awaits user source, T15/T16 remain next.
