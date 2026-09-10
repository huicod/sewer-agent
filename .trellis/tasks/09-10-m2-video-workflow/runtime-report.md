# T08 runtime and handoff

## Delivered code

- Video upload/text inspection now dispatches a real Temporal video workflow. Default
  app/init/worker use VideoRepository; image/supplement behavior remains available.
- One serial GPU activity sequentially detects every decodable frame; atomic PostgreSQL
  chunks preserve observations, event head, cursor and displayed frame/chunk progress.
  Heartbeat/finite retries resume without re-inference of the committed prefix. CPU
  prefix re-decode still costs time, but neither old PNGs nor old GPU calls are needed.
- Sparse second-pass evidence creates only selected lossless originals, with deterministic
  frame AssetRefs parented to the video and original PTS/pixel coordinates. Per-frame
  API checkpoints and bounded review selection (default 32, <=3 attempts/frame) are
  independent of scan retries. Full frame observations never enter Temporal history.
- Conditional event review and periodic negative audits reuse image rules. Incomplete
  full-image reviews, conflicts, missing budget/coverage, and OTHER remain pending/partial.
- Cancellation uses a durable Temporal Update with an explicit accepted/refused result;
  active activity cancellation is acknowledged before terminal publication. Reporting
  closes cancellation admission (HTTP 409 `finalizing`), terminal state cannot be rewritten.
- UI shows actual phase, frames/chunks, evidence time and a cancel button. GET task video
  metadata and JSON/HTML reports read saved facts, without rerunning tools.

## Real CCTV evidence

29 original videos extracted from the two supplied RARs under ignored `var/datasets`,
total 3:24:07, about 289,171 declared frames. Original archives were not modified.

Full shortest recording `W90-W89_20260611151716.mp4`: 22.401953 s, **561/561 frames**,
71 committed chunks, complete decoding, 2 candidate events from 5 detections. Event
intervals: one-frame PL at 3.801953 s and FS at 20.521953–20.762969 s; neither short
candidate was dropped. Four original evidence frames, four real Qwen API attempts.
These are candidates on unlabelled video, not proven defects or event-accuracy measurements.

Actual test process PID 183728 was terminated with SIGTERM while detecting at frame
cursor 248, leaving Temporal/PostgreSQL running. The replacement worker reopened the
same run and workflow: DB attempt 1 has exactly frames 0–247/31 chunks; attempt 2 has
248–560/40 chunks. No repeated committed range. Resume invocation took 329.73 s including
heartbeat expiry, remaining scan, evidence and API review; it is not fresh-scan throughput.

This pilot deliberately capped reviews at **4** (production default 32), so a missing
required audit/conflicting advice yielded **partial / inconclusive**, not normal.
Artifacts: `var/evaluations/2026-09-10/video-short/{run,summary,task,scan,evidence,calls}.json`
and original/derived `assets/`. Its isolated PG schema is recorded in run.json.

Second full recording `videos-2/WS10-1_WS10-2_20260612101325.mp4` (147.922969 s):
**3,698/3,698 frames**, 463 committed chunks, complete decoding with no decode error.
397 detection observations became **77 candidate visible segments** and **83 persisted
evidence frames**; these are not 77 proven physical defects. Four real Qwen attempts,
with the same explicit 4-frame API cap. Result: **partial / defects_found** under the
current rules, with inadequate quality, added suggestions and missing required reviews
explicitly retained. It is not a full review or labelled video-accuracy result.
Execution took **1,397.01 s (~23 min 17 s)** excluding upload/admission, about 2.65
processed frames/s including evidence/API stages; no real-time throughput claim.
Same artifact layout under `var/evaluations/2026-09-10/video-long`.

Combined: **4,259 original frames**, 534 unique committed chunks, 79 candidate segments,
87 evidence frames and 8 video API attempts. With the image pilot, this task made
47 actual review calls/attempts. The new videos are deliberately not run with unlimited
per-frame VLM; complete detector coverage does not imply complete VLM coverage or zero misses.

## Verification and remaining boundaries

Focused actual PG/Temporal tests passed: lost commit acknowledgement + resume, cached
evidence/review, CPU-worker replacement after scan, queued cancellation and explicit
cancellation refusal during final commit. Independent review found and TDD-fixed the
incomplete-event-audit false-normal path, terminal scan writes, and finalization cancel race.
Final regression: **513 backend tests passed, 7 skipped** in 64.41 s with
`SEWER_RUN_STORAGE_TEST=1 SEWER_RUN_IMAGE_WORKFLOW_TEST=1 SEWER_RUN_CONVERSATION_TEST=1
SEWER_RUN_VIDEO_WORKFLOW_TEST=1 .venv/bin/pytest -q backend/tests`.
Frontend: **32/32 tests**, TypeScript/Vite build passed. Live provider/GPU evidence is
the separately recorded real pilot, not simulated by these deterministic tests.

One full-run fault test exposed a test-phase race: the PostgreSQL scan checkpoint is
written before Temporal acknowledges Activity completion. The CPU-only replacement
case now waits for the workflow's acknowledged `aggregating` phase before removing
the GPU worker, instead of assuming checkpoint existence means no GPU acknowledgement
can remain. The separate lost-ack test and real SIGTERM run retain replacement GPU workers.

No new package/framework, detector training, prompt/threshold tuning, checksum pipeline,
or shared-service restart. Temporary PNG compression was profiled and changed to lossless
level 1; evidence-only decoding skips PNG encoding of unselected frames. Full GPU scanning
still encodes temporary images and is not real-time throughput on these 1080p clips.
True human review/waiting UI and precise timeline remain T09; YOLOE/tools remain later
tasks. Independent full-five-class accuracy gain is **not** an accepted capability.

## Running the updated application

From the repository, initialize the two new tables once with `.venv/bin/python scripts/init_storage.py`.
Use the existing local CPU and GPU worker commands `scripts/inspection_worker.py cpu` / `gpu`
with `.venv/bin/python`; restart application workers after code changes, not the shared
Temporal/PostgreSQL services. Tests used isolated schemas/queues; this task has not
exposed a public server or changed the user's credentials.
