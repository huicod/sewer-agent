# Reusable real-video evidence inventory

## Recommendation

Freeze the eight already-reviewed PNGs below (four per real video). This is the only repo-local
set that has both a persisted original frame and a succeeded `qwen3.8-max` API call. Treat all
labels/statuses as saved detector/VLM/policy outputs, never as human ground truth.

| ID | frame / PTS | exact load path | saved relation/outcome |
|---|---:|---|---|
| S49 | 49 / 1.921953 s | `/root/autodl-tmp/server/var/evaluations/2026-09-10/video-short/assets/a3f5e50f-7c7f-5a9d-9ad9-f183081f5807/1/source` | periodic audit (no event); VLM-added `OTHER` suggestions `沉积/结垢`, `水位/流动`, both `needs_review` |
| S96 | 96 / 3.801953 s | `/root/autodl-tmp/server/var/evaluations/2026-09-10/video-short/assets/96dd1aa7-934e-5f76-aeeb-5eebed103013/1/source` | one-frame detector event `PL`, score 0.283026; final `needs_review` |
| S346 | 346 / 13.801953 s | `/root/autodl-tmp/server/var/evaluations/2026-09-10/video-short/assets/139cacd7-ea46-5281-882d-bc7950937b3c/1/source` | periodic audit (no event); VLM-added `FS` / `Surface Corrosion/Scaling` and `OTHER` / `Joint Offset/Gap`, both `needs_review` |
| S520 | 520 / 20.762969 s | `/root/autodl-tmp/server/var/evaluations/2026-09-10/video-short/assets/119032cd-692e-5360-a37f-db97c54fe9f2/1/source` | detector `FS` event frames 514–520, 4 observations; final `rejected` |
| L29 | 29 / 1.165000 s | `/root/autodl-tmp/server/var/evaluations/2026-09-10/video-long/assets/b54ff43b-dbfa-52d7-9b27-59cbbed895f4/1/source` | detector `BLOCK` event frames 25–29, 2 observations, final `rejected`; VLM-added `OTHER` / `Invalid Scene` is `needs_review` |
| L52 | 52 / 2.082969 s | `/root/autodl-tmp/server/var/evaluations/2026-09-10/video-long/assets/c51850d7-7dcc-5225-9ace-dd48e0b3e73e/1/source` | one-frame detector `BLOCK`, score 0.268920, final `needs_review`; VLM-added `OTHER` / `Invalid View / Non-Pipe Scene` |
| L330 | 330 / 13.202969 s | `/root/autodl-tmp/server/var/evaluations/2026-09-10/video-long/assets/f42da841-dd1c-565c-8289-080eea8ceac1/1/source` | detector `BLOCK` event frames 323–330, 6 observations; final `confirmed`, `confirmed_by:"rules"` (not human) |
| L416 | 416 / 16.643984 s | `/root/autodl-tmp/server/var/evaluations/2026-09-10/video-long/assets/a54fe88d-c66e-54f0-a681-c8a675917dff/1/source` | one-frame detector `AJ` final `rejected`; VLM-added `BLOCK` / `Debris/Obstruction` and `FS` / `Corrosion/Deposits`, both `needs_review` |

All eight files are lossless 1920×1080 RGB PNGs despite the extensionless `source` filename.

## Video/run context and coverage

- Short source: `/root/autodl-tmp/server/var/datasets/videos-1/W90-W89_20260611151716.mp4`;
  22.401953 s, 561/561 frames, 2 events/5 candidate observations, 4 evidence frames and 4
  succeeded VLM calls. The fixed set covers both event representatives and 2/3 periodic audits;
  audit frame 551 was excluded by the four-call cap. Coverage flags are `true,true,false`
  (`sufficient_coverage`, `sufficient_quality`, `negative_audit_complete`).
- Long source: `/root/autodl-tmp/server/var/datasets/videos-2/WS10-1_WS10-2_20260612101325.mp4`;
  147.922969 s, 3698/3698 frames, 77 candidate events/397 observations, 83 evidence PNGs and
  4 succeeded VLM calls. The fixed four are event-first selections and span only 1.165–16.644 s
  (the first 11.3%); no periodic audit was reviewed. Flags are `true,false,false`. They are useful
  cases, not representative temporal coverage of all 77 events.
- Pins in both `run.json`: detector `sewer2403_5class_yolo26s_img960_e100/best`, VLM
  `qwen3.8-max`, prompt `image-review-v1`, image policy `image-policy-v1`, video policy
  `video-review-v1`, review budget 4.

## Repo-local schemas and keys

- `evidence.json`: `{frames:[{asset:{asset_id,version},frame_index,timestamp_seconds}],
  review_frames:[int]}`. Asset path rule is `assets/<asset_id>/<version>/source`.
- `scan.json`: `{state,error_code,decode_complete,inspection_result}`. `state.events[]` has
  `{event_id,label,start_frame,end_frame,start_seconds,end_seconds,observations,last_region,evidence}`;
  event evidence has `{frame_index,timestamp_seconds,detection_index,score,width,height,region,quality}`.
  Example event key: `<video-asset-id>:v1:<start-frame>:<ordinal>`.
- `task.json.snapshot.findings[]`: `{finding_id,canonical_label,open_label,review_status,source,
  score,evidence,confirmed_by}`; this is the policy-derived output used in the table.
- `calls.json[]`: API steps use `step_id:"video_verify:<frame_index>"`, with
  `{model_version,input_assets,status,error}`. All eight selected steps are `succeeded` and have
  empty `output_assets`.
- Expected raw `ImageReview` contract is `{image_assessable,full_image_audited,candidates:[{index,
  verdict,proposed_label,reason}],additional_anomalies:[{label,name,bbox_xyxy,reason}],summary}`.

## What is and is not reusable

`run_real_video.py` exported aggregate `scan.json`, selected assets, call records and final findings,
but did **not** export PostgreSQL `FrameObservation` rows or `video_verify:<frame>` checkpoint bodies.
Thus the repo has event evidence/quality summaries and policy-derived VLM effects, but not the complete
raw per-frame detection batch or raw `ImageReview` response. Searching the selected asset IDs finds
only each run's `calls.json`, `evidence.json`, and `task.json`.

Therefore a fixed qualitative baseline/gallery can make **zero new paid calls** using these eight PNGs
and saved outcomes. The JSON exports alone cannot provide exact raw-verdict replay; the live checkpoint
recovery below now supplies that missing cached baseline. A genuinely fresh model/prompt output would
still require a separately existing cache or a new call.

There is no `var/videos/` directory. The Sewer2403 `extracted/{images,labels}` tree is a separate
labeled still-image corpus and provides no identity mapping to these two videos. Neither video export
contains `ground_truth`, an annotation manifest, or human confirmation (`confirmed_by` is only null or
`"rules"`); both `summary.json` files explicitly store `accuracy:null`. Consequently video/frame/event
accuracy, precision, recall, F1, miss rate, and improvement versus truth cannot be computed. Only
descriptive output counts, status changes, coverage flags, and side-by-side qualitative differences
are supportable.

## Read-only database follow-up (2026-09-11)

Both isolated PostgreSQL schemas remain readable. Retrieval through `VideoRepository` recovered and
validated 8/8 selected `FrameObservation` rows and 8/8 `video_verify:<frame>` checkpoint bodies as the
base `ImageReview` contract; no review errors or missing rows were found and no database write or model
call was made. The sanitized cache is
`/root/autodl-tmp/server/var/evaluations/2026-09-11-incremental/video-cache.json`.

It contains root keys `{samples,provenance}`. Each sample retains prompt `image-review-v1`, exact
`ImageObservation` detection/quality, base review, `ground_truth:null`, and
`baseline_policy:"saved-video-review-v1"`. `baseline_predictions` contains six total five-class,
non-rejected saved final boxes matched to the same evidence frame (missing VLM scores become 1.0);
identical label/box deduplication removed 0 entries. `changes:[]` deliberately avoids inventing
guided-review strength/change semantics for these older video reviews. Human-GT limitations remain.
