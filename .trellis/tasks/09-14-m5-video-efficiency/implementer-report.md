# Implementer report: temporary video frame format

## Result

Implemented the approved, pixel-preserving separation between temporary scan frames and persistent evidence:

- `decode_video_chunks(..., frame_format="PNG")` now accepts only the exact values `PNG` and `BMP`.
- Invalid frame formats raise `VideoDecodeError("invalid_options")` before `tempfile.mkdtemp` is reached, so no decoder scratch child is left behind.
- The default remains PNG with `compress_level=1` and a `.png` suffix.
- BMP uses Pillow's uncompressed BMP encoding with a `.bmp` suffix and no compression option.
- `scan_video` explicitly requests BMP for temporary detector/quality inputs.
- `extract_scan_evidence` and the other decoder callers remain on the default PNG path, preserving persistent evidence behavior.
- The business-restart detector fixture recognizes both temporary `.png` and `.bmp` frame names.

No detector settings, pixels, PTS handling, chunk size, coverage, aggregation, evidence naming, recovery semantics, dependencies, API/configuration, VLM policy, or service behavior were changed.

## TDD evidence

Tests were added before production code.

The first RED run exposed an accidental missing `Path` import in the test fixture in addition to the intended failures. That test-only error was corrected before any production edit, then RED was rerun:

```text
.venv/bin/python -m pytest backend/tests/test_video_decode.py backend/tests/test_video_scan.py -q
5 failed, 38 passed in 4.56s
```

The five expected failures were:

- two decoder tests because `frame_format` did not exist;
- both batch modes of the full-frame scan because detector inputs were still `.png`;
- the resume scan because detector inputs were still `.png`.

After the minimal implementation, GREEN was:

```text
.venv/bin/python -m pytest backend/tests/test_video_decode.py backend/tests/test_video_scan.py -q
43 passed in 4.53s
```

The tests use actual FFmpeg-created lossless video and actual Pillow files. They verify default PNG format/suffix and `compress_level=1`, BMP format/suffix and BMP header compression code `BI_RGB` (`0`), byte-identical decoded RGB pixels, identical PTS, invalid-format cleanup, scan-time BMP inputs, final PNG evidence, full coverage, resume behavior, and existing decoder failure/close cleanup cases.

Scoped lint evidence:

```text
.venv/bin/ruff check backend/src/pipe_inspector/vision/video_decode.py backend/src/pipe_inspector/vision/video_scan.py backend/tests/test_video_decode.py backend/tests/test_video_scan.py backend/tests/business_restart_worker.py
All checks passed!
```

## Files changed

- `backend/src/pipe_inspector/vision/video_decode.py`
- `backend/src/pipe_inspector/vision/video_scan.py`
- `backend/tests/test_video_decode.py`
- `backend/tests/test_video_scan.py`
- `backend/tests/business_restart_worker.py`
- `.trellis/tasks/09-14-m5-video-efficiency/implementer-report.md`

## Review and remaining checks

Self-review confirmed that the only initial-scan decoder call opts into BMP. `extract_scan_evidence` and `VideoActivities.prepare_video` omit `frame_format`, so persistent evidence continues to use the PNG default. The decoder still owns and deletes one bounded chunk at a time.

Per the task boundary, I did not run the full backend suite, GPU/live model experiments, service-backed restart acceptance, paid APIs, or benchmarks, and I made no VCS mutations. The main agent owns those checks, the old/new real-video comparison, documentation, Trellis lifecycle, and jj checkpoint. The updated restart fixture is therefore linted but its disruptive service integration is intentionally not run here.

No implementation concern is currently known. The approved resource tradeoff remains: an 8-frame 1080p BMP scratch chunk uses more temporary disk space than PNG but remains bounded and is cleaned by the existing lifecycle.
