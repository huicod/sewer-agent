# T03 detector lifecycle — implementation brief

Own only `backend/src/pipe_inspector/vision/detector.py`, new `backend/tests/test_detector_lifecycle.py`, and this task's detector-report.md. TDD; no nested agents, package changes, exports, hashes, model downloads or Git/jj mutations.

Main architecture 10.8 requires metadata, capabilities, explicit loading/warmup, image/batch inference. Existing DetectorAdapter already supplies detector_id/model_version/label_space_version, class_names and predict_image/predict_batch.

Add immutable `DetectorCapabilities` dataclass with supports_batch: bool and coordinate_space: Literal["original_pixels"]="original_pixels". Add capabilities property/attribute to Protocol and UltralyticsDetector (supports_batch=True), `load() -> None`, `warmup(image_path: str|Path) -> None`. load uses existing guarded lazy loader; warmup performs one inference on an explicit existing representative image, discards output and creates no implicit files. No synthetic media or extra weight imports at module scope.

Validate replacement with a test-only independent adapter implementing the same Protocol, returning DetectionBatch, and a parametrized consumer exercised on both the real Ultralytics adapter with a vendor-boundary fake and the independent test adapter. Assert canonical output, original-pixel coordinates and lifecycle behavior; missing checkpoint must still fail before vendor loader. The test-only replacement is not a second production model.

Real trusted GPU smoke has already passed this turn (AJ score0.93179); do not rerun it. Focused tests/lint, record RED/GREEN and report. Root handles exports and shared geometry independently; do not edit detection.py.
