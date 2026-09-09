# T02 detector adapter subtask

Own `backend/src/pipe_inspector/contracts/detection.py`, `backend/src/pipe_inspector/vision/detector.py`, package init files, `backend/tests/test_detector.py`, `scripts/detector_smoke.py`, task detector-report.md. No config/packaging/task-state/jj edits.

Pydantic detection contract: canonical five-class label AJ/BX/FS/PL/BLOCK; native_label; finite score 0..1; bbox_xyxy in original-image pixels. Batch: detector_id, model_version, label_space_version, width/height, detections. Validate ordered finite coordinates and bounds. No NORMAL/UNCERTAIN object labels, no model-specific results outside adapter.

Define DetectorAdapter Protocol with metadata and predict_image(path), predict_batch(paths). First adapter UltralyticsDetector accepts checkpoint, device, imgsz=960, conf=0.25; imports/loads model lazily. Translate class IDs via actual model.names (not assumed numeric order), validate all labels are the trained five, and output plain contract values. No global model instance, hashing, training, downloads, tracker, alternate detector or auto file writes.

TDD: lightweight contract/translation tests with realistic model-result fixtures at vendor boundary; bad labels, invalid boxes/scores and missing input behavior. After root installation completes, run one real CUDA inference on `var/fixtures/sewer2403-sample.jpg`, existing best.pt per detector-probe.md. Record model classes, detection count, elapsed time and actual output in ignored var/fixtures/detector-smoke.json. This single AJ sample is a compatibility smoke, not an accuracy benchmark.

Use .venv/bin/python; root owns dependency install (`ultralytics==8.4.46`). scripts/detector_smoke.py should take explicit --image, --checkpoint, --device and print JSON, with optional output destination only if needed. No live API. No nested agents. Report <=80 lines with tests and exact inference evidence; integration must not silently skip and count as passed.
