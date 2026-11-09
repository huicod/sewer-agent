# Interactive video processing timeline

The user requested the video processing time-axis component after accepting the engineering workbench. Preserve the current approved visual style and all saved records.

## Boundary and implementation
- Replace the workbench's collapsed text-only timeline entry with an always-visible, compact visual time axis underneath the video. Retain detailed scan/review information and the existing exact-frame tool picker as an expandable section.
- Draw actual persisted scan extent, candidate intervals and saved evidence points against real video duration. Read actual persisted in-flight scan state when available through an additive API field; never derive a timestamp from task percentage or assume reviewed/confirmed status from selection.
- Link playback time and timeline seeking without autoplay, inference, arbitrary-frame admission or implicit conversation-frame changes. Keep source/version isolation, errors/retry and keyboard/mobile access.
- Main owns TaskCard/TaskInputPreview/VideoTimeline integration, types/validation coordination, tests, jj and deployment. Visual worker owns only new timeline component/CSS/tests. Any backend worker owns the additive progress endpoint/contracts/tests only.

## Acceptance
- Clicking candidate/evidence positions seeks the original video; the exact selected saved frame can be previewed and independently chosen for tools.
- No-duration/no-scan/partial/error/one-frame/overlap cases remain readable and truthful. Failed progress reads do not fabricate an empty successful scan.
- Proportionate frontend and backend regressions, type/build and independent review pass; browser verifies real saved video, scrubbing, evidence source and narrow layout.
- Existing local session/task snapshots remain unchanged, local deployment updated and latest dev/agent-v1 synchronized under existing authorization.
