# Frontend unit requirements

Read design.md Shared interface and UI/video sections; they define exact HTTP contracts.
Implement T09 review/evidence UI only in frontend/src (+tests). Root/backend implement API.

Create focused ReviewPanel.tsx and VideoTimeline.tsx, integrate existing TaskCard without
restructuring whole App. Extend types/api/validators with exact design.md schemas. Backend
already provides GET /api/tasks/{id}/video with {task_id,scan,evidence}; inspect contracts/video.py
and workflow/video_activities.py read-only for actual event/frame shapes, no guessed IDs.

ReviewPanel waiting state: display current findings, labels/status, source and native box
over original evidence image (SVG viewBox + img/image using existing same-origin content URL),
all original timestamps. Controls confirm/reject/relabel; five labels plus OTHER; OTHER name;
reason required; reviewer defaults huicod (self-declared), optional note/audit checkbox.
Pending items no automatic confirmation selection. Existing confirmed/rejected may prefill
their status, but actions/reasons still explicit enough for audit. Submit complete finding set
with snapshot_updated_at from loaded detail. Disable duplicate click while in flight; failed
identical retry reuses same request_id; changing input generates new UUID. No client-derived
final conclusion. Refresh accepted server record/snapshot and expose read-only audit when
task terminal. Session reload restores server waiting/history. Handle stale response/task
switch so no review is applied/displayed on another task. Evidence/assets loaded lazily.

Use GET review on user opening panel (not every task/poll); avoids unnecessary traffic and
old fake tests depending on unrelated routes. Button on waiting opens review. Terminal tasks
may have '查看审核记录' button that opens read-only detail (record None is valid historical).
Replace obsolete '未解决项尚无人工处理入口' copy with truthful partial/legacy text.

Timeline: user expandable section for tasks known to be video (can discover from evidence
frame locators or source AssetRecord GET; existing intake GET asset metadata available).
Show persisted scan.state.events (and active only if incomplete) sorted by source seconds,
time interval/frame count and evidence-frame links; visually label candidate timeline, not
confirmed defects. Show decode_complete/coverage errors honestly. No new detector/video pass.
Frame preview uses assets from evidence.frames matched by frame_index; don't invent links
from event IDs. Optional native video player is fine but evidence links must work if codec
unsupported; don't infer timestamps from FPS. No new dependency or public binding.

TDD + HTTP-boundary tests for disabled incomplete review, confirm/relabel/reject payload,
same retry ID vs edited body, durable history display, evidence coordinates and real intervals.
Run Node via PATH="$PWD/.tools/node/bin:$PATH" from repo root; frontend tests and build.
No backend/VCS edits or nested delegation. Write frontend-report.md in this task with concise
RED/GREEN/test results. Don't add new frameworks or speculative app navigation.
