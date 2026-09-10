# T07 实现计划

Goal: implement the approved T07 video initial-scan core and a separately reported paired image review pilot.
Architecture: FFmpeg streaming decoder → bounded PNG batches → existing detector adapter → deterministic serialized event reducer → candidate/evidence manifest. No video HTTP/Temporal dispatch until T08. Trellis is the sole task ledger; root owns jj/integration, delegates have disjoint write scopes.
Spec: prd.md; main architecture §§9–10 and DEVELOPMENT_PLAN.md T07. Python3.12, existing FFmpeg4.4/Pillow, no new runtime dependency.

## Units and tests

1. Decoder delegate: `vision/video_decode.py`, `tests/test_video_decode.py`. `DecodedFrame(index:int, timestamp_seconds:float, width:int, height:int, path:Path)` and `decode_video_chunks(source:Path, *, chunk_size=8, timeout_seconds=30, max_pixels=40_000_000, scratch_root:Path|None=None) -> Iterator[tuple[DecodedFrame,...]]`. Full video sequential pass, original PTS, files valid until iterator advances; cleanup only its temporary dirs/process. Missing/bad PTS, nonmonotonic timestamps, resolution/byte errors, unsupported inputs, timeout or ffmpeg failure raise `VideoDecodeError(code)`, never guess FPS. Real CFR/VFR/offset-PTS fixtures, batch limits, close cleanup, safe failures. No dataset/API/GPU/VCS writes.
2. Root: `contracts/video.py` + `vision/video_events.py` + tests. FrameObservation contains index/source timestamp, DetectionBatch, QualitySummary. Aggregation state contains source AssetRef, pinned rule/model metadata, next_frame, active/completed events, periodic audit frames. `aggregate_chunk(state, observations, *, final=False)` is pure; contiguous new indices, prefix replay ignored under same source/config, backward time/gaps rejected. Same-class greedy IoU matching with fixed temporal gap; normalized coordinates across sizes; stable IDs from source ID/version + first frame/detection index (not content hashes). Keep one-frame events, at most3 temporal-diverse evidence records and one quality-best audit frame per10s. Test hand-checked matching, class isolation, gaps, replay/reopen, short events, policy pins and evidence bounds.
3. Root: `vision/video_scan.py`, `scripts/video_scan.py` + tests. Stream decoder to predict_batch (or per-image fallback when capability false), quality analysis and reducer; no proprietary detector result use. CLI requires explicit source/checkpoint/output, bounded batch, pins config and writes frame observations JSONL plus final candidate/coverage manifest. Detector failure/decode error preserves partial records and reports incomplete, no normal claim. Selected evidence copied using a second decode-only pass, never rerunning detector; first version favors bounded storage over optimized seeking. No production DB/task state changes.
4. Evaluation unit: inventory existing labels/splits before specifying fixed pilot. Separate protocol/brief in this task, evaluation files only; no production policy tuning. Root authorizes/runs bounded live requests only after sample/API policy fixed. Unit tests for paired metrics and resume avoid repeated API calls.

Checks (each code unit uses RED → minimal GREEN → relevant regression):
- [x] Decoder real FFmpeg fixtures, partial-prefix failure and bounded cleanup.
- [x] Events/contracts hand-computed replay/matching/coverage tests.
- [x] Scan integration and explicit GPU smoke; no frontend video activation yet.
- [x] Paired evaluation protocol/tool/metrics/resume tests; incomplete runs do not emit comparisons.
- [ ] Real paired pilot: awaiting verified held-out manifest or independent labelled images; no API batch selected/run.
- [x] Independent scoped review and fixes; final backend460 passed/6 skipped; progress saved in runtime-report.md and jj change mzwqotpq.

Open evidence question (asked): original split_manifest.csv or clearly untrained labelled images. True-normal labels and real CCTV video are also absent. Existing five-class empty labels must not be treated as true-normal. API test cost is bounded by selected sample count; do not silently run the full test set.
