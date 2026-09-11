# T09 Implementation Plan

> Use subagent-driven development with Trellis state and disjoint file ownership;
> repository overrides replace worktrees, duplicate ledgers and repeated review gates.

**Goal:** Human review actually suspends and resumes image/video workflows with audit.
**Architecture:** Existing Temporal updates + Activities; atomic PostgreSQL projection;
React evidence/review panel and existing video metadata timeline.
**Tech Stack:** Existing FastAPI, Pydantic, psycopg, Temporal, React/TS/Vite.
**Spec:** design.md and approved DEVELOPMENT_PLAN.md T09.

## Global constraints

- No new dependencies, hashes, paid API/model experiments or remote pushes.
- Preserve old run semantics unless `human_review_version="human-review-v1"` is pinned.
- Temporal owns admission/order; HTTP never directly applies the final snapshot.
- Exactly one immutable audit per task; identical UUID submissions retry idempotently.
- Human classification does not manufacture coverage, calibrated dimensions or identity.

## Tasks and interfaces

| Unit | Produces / consumes | Owner / nonoverlap |
|---|---|---|
| 1 Backend | contracts/repository/HTTP + HumanReviewStarter; consumes existing task/asset schema | backend agent; no existing workflows/start.py or frontend |
| 2 Workflow | run pin, wait/update/cancel, apply Activity; consumes unit 1 submission/repository | root; no unit 1 files |
| 3 UI | ReviewPanel + VideoTimeline; consumes fixed HTTP contracts in design.md | frontend agent; frontend only |

The three units agree on complete-batch review and request identity. Existing `final`
checkpoint must not overwrite the post-review terminal snapshot; unit 2 checks this.
No counterpart needs a newly invented message broker, workflow or model interface.

### 1 Backend boundary (backend-brief.md)

- [x] RED: reject stale/partial/foreign decisions, OTHER without name, conflicts; confirm /
  relabel / reject preserve original audit; duplicate requests return exact persisted record.
- [x] GREEN: implement contracts, pure policy, atomic repository, start boundary and routes.
- [x] Verify focused unit + isolated PostgreSQL tests, including concurrent submissions.

### 2 Persistent workflow integration (root)

- [x] RED: new run waits after automate finish; old pinless run still terminates; duplicate
  finish cannot revert reviewed state; workflow review submission persists before completion.
- [x] GREEN: shared workflow review gate and Activities, pin new starts, connect image,
  supplement and video finish, safe waiting cancellation, worker repository/registration.
- [x] Verify `backend/tests/test_human_review_workflow.py` and affected workflow tests;
  real PG/Temporal worker replacement while waiting, then confirm + duplicate submission.

### 3 Evidence and review UI (frontend-brief.md)

- [x] RED: pending item cannot submit without action/reason; evidence overlay coordinates;
  exact retry identity; submitted history; server waiting stage restored; event intervals.
- [x] GREEN: add focused components, HTTP validators/types and integrate TaskCard.
- [x] Verify with `PATH="$PWD/.tools/node/bin:$PATH" npm --prefix frontend test -- --run`
  and `PATH="$PWD/.tools/node/bin:$PATH" npm --prefix frontend run build`.

### 4 Integration / handoff

- [x] One independent review of combined diff against design, correct reachable findings.
- [x] `.venv/bin/pytest backend/tests -q --tb=short`, relevant Ruff/format, frontend suite/build.
- [x] Save actual local recovery test evidence, concise report, roadmap and Trellis journal;
  freeze jj checkpoint and leave next empty change. No automatic deployment or push.
