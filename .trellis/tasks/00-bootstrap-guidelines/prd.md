# T01 — Development harness and backend bootstrap

User approved DEVELOPMENT_PLAN.md on 2026-09-09. This replaces the generic bootstrap checklist; it does not add a second roadmap.

Scope: initialize Trellis for huicod, preserve AGENTS.md, disable automatic Git commits, establish jj task boundaries, project virtualenv and minimal backend test entry.

Acceptance:
- Trellis identity and active task persist; hooks are not silently trusted.
- Local secrets, assets, weights and environment outputs stay outside version history.
- Backend health reports liveness only; configuration masks credentials and does not assume external APIs are ready.
- Tests pass and actual conventions have a short entry point.

Frontend rules will be filled when frontend code exists, not invented during setup. No training, live API calls, remote pushes or business implementation in this task.
