# T01 implementation evidence

- Trellis 0.6.15 initialized as huicod with existing AGENTS.md preserved; project workflow overrides use jj and session_auto_commit=false. Hook trust remains manual.
- Project .venv reuses existing torch/CUDA via system-site-packages; all new dependencies installed into .venv, no base modifications.
- Created FastAPI liveness endpoint, masked Settings and three tests. RED: three tests failed on missing app/config imports before implementation. GREEN: `.venv/bin/python -m pytest backend/tests -q`: 3 passed, 0 warnings; `.venv/bin/ruff check backend`: all checks passed.
- Initially Starlette's TestClient emitted a deprecation warning; tests now use existing httpx ASGITransport directly. Generated egg-info was untracked and remains local.
- Actual conventions live in .trellis/spec/backend/conventions.md; unused frontend templates are not claimed as implemented code.
- T02 investigations are separate from T01 completion. No live API, GPU inference or durable runtime claim yet. No remote push.
