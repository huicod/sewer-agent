# T06-A evidence-report implementation
- Scope: versioned TaskReport, GET /api/tasks/{id}/report?format=json|html, deterministic escaped HTML and read-only API registration.
- Sources: validated stored TaskRecord plus ToolCall attempts. No new detection/model call, no rewriting conclusion. Nonterminal snapshot returns report_not_ready (409); unsupported format 422; unknown task 404.
- RED: .venv/bin/pytest -q backend/tests/test_reports.py --tb=short — 4 expected failures (endpoint absent, 404).
- GREEN: the 4 endpoint tests passed after implementation and correcting a test double to use the existing zero-argument MissingRecord constructor. Added normal/failed and foreign-task-call cases, and real-PG API read after repository reopen.
- Combined evidence: SEWER_RUN_STORAGE_TEST=1 .venv/bin/pytest -q backend/tests/test_reports.py backend/tests/test_qwen_configuration.py backend/tests/integration/test_inspection_storage.py --tb=short — 26 passed (3.25s); targeted Ruff passed.
- Real-PG test creates/drops only its UUID-named test schema, preserves the saved task snapshot and sends no API requests. No shared service restarted.
- Boundary: report API exists; chat UI, persisted multi-turn messages and supplemental-operation execution belong to T06-B and are not implemented by this slice.
