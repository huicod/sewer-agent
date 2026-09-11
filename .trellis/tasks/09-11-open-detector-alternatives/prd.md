# Alternative open detectors — bounded image experiment

## User requirement / scope

Keep the accepted VLM review, primary five-class detector and fusion unchanged.
Try reliable, strong public open-vocabulary detectors in place of the weak YOLOE
supplementation branch. No rollback is necessary; reuse the existing adapter and
incremental evaluator. T12 enhancement work stays paused; no production switch.

## Decisions

- Compare MM-Grounding-DINO Base O365/GoldG/V3Det and OWLv2 Large ensemble; source
  evidence and exact revisions are in `research/models.md`.
- Reuse the same 48 Sewer2403 images, original pixels, cached primary/VLM results,
  seven `sewer-open-v1` text concepts and conditional trigger as T11. Exclude its
  eight video probes. Never call an inference API or tune against ground truth.
- Model-native resolution and declared thresholds: MM-GDINO 800/1333, 0.30;
  OWLv2 1008 square, 0.10. These scores are not calibrated across models.
  MM uses declared mean sigmoid over each concept's known token span (OpenMMLab
  positive-map semantics), not HF's generic max-token phrase scorer. MM runs FP32
  after a real FP16 type-conflict smoke failure; OWLv2 uses native eager FP16.
- Deterministic same-concept NMS at IoU 0.5 and at most 100 candidates for the new
  models. Preserve original-coordinate boxes, OTHER, and pending-review semantics.
- Use local safetensors and pinned optional Transformers 4.57.6. Download only
  official public assets during setup; adapters are lazy and offline at runtime.
- Reuse each model during its bounded offline run, release it finally. Existing
  production unloading and the T11 evaluator's defaults remain unchanged.

## Work / acceptance

1. Add a tested HF adapter implementing the existing OpenDetectorAdapter contract;
   test coordinate/label mapping, invalid output, duplicates, offline loading.
2. Add a small reproducible 48-image comparison entry point, retaining checkpoints,
   existing stage galleries, review queues and summaries; reuse the old YOLOE run.
3. Run both models locally; report actual candidate gains/failures relative to the
   accepted VLM baseline. Incomplete source annotations are not human ground truth;
   pending candidates are not confirmed accuracy gains. Supply per-image evidence.
4. Run relevant regression tests and one integrated review, then save progress in
   Trellis and jj without pushing. Leave production defaults and VLM code untouched.

## Ownership / interface check

- Adapter agent: `vision/hf_open_detector.py` and its unit tests only.
- Main: comparison entry point/tests, environment setup, real experiments, delivery.
- Both consume the unchanged OpenDetectionBatch protocol; no shared write files.
- One combined review covers adapter, comparison and evidence; no new workflow ledger.
