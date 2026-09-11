# T09 durable human review and video evidence timeline

## Goal

Resume approved roadmap at T09: a usable human-review entry that persists evidence and
decisions, truly waits across worker failures, resumes without duplicate application,
and exposes existing video event evidence in the UI.

## Requirements

- New image, linked review and video runs with unresolved findings/inconclusive evidence
  wait for manual review without occupying model resources; old runs remain historical.
- Support confirm, relabel and reject with reviewer/note, stable finding/evidence identity,
  immutable before/after audit and idempotent submission. Never silently reopen terminal tasks.
- Serve review evidence and accepted history; display native-coordinate image boxes and
  existing source-timestamp video events without new processing.
- Negative human audit cannot manufacture missing coverage/quality or physical measurements.
- Keep model iteration stopped, no API/GPU experiment, no framework/dependency expansion,
  no secrets, remote pushes or changes to historical experiment/dataset assets.

## Acceptance Criteria

- [x] Complete batch review and error/idempotency contracts tested at policy/HTTP/DB boundaries.
- [x] Actual waiting survives worker replacement and accepts review once; cancel race safe.
- [x] UI supports evidence, decisions, unchanged retry identity, server history and timeline.
- [x] Backend/frontend checks, focused independent review, report and jj/Trellis checkpoint.

## Notes

User reauthorized original roadmap on 2026-09-11; architecture/plan already approved.
Implement current stage only; user acceptance of image optimization is recorded, not a
claim of independently measured accuracy or authorization for more paid experiments.
