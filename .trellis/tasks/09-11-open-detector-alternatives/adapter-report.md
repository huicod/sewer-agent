# HF open-detector adapter report

Implemented `HFOpenDetector` for the pinned `mm-gdino-base` and `owlv2-large`
checkpoints without changing the shared detector protocol or production wiring.

## Decisions

- MM-Grounding-DINO uses the exact seven-concept caption and tokenizer offset
  mappings. Each query/concept score is the mean sigmoid probability over the
  concept's known token span; the highest-scoring concept is selected. Decoded
  label strings are never used for mapping.
- OWLv2 uses the Transformers 4.57.6 grounded-object postprocessor with the
  original `(height, width)`, preserving its padded-square coordinate handling;
  returned query indices map directly to the fixed vocabulary.
- Both paths apply score-stable, same-concept NMS at IoU 0.5, clamp finite edge
  overflow, drop degenerate clipped boxes, reject malformed/nonfinite output,
  and cap output at 100.
- Processor selection is explicit: MM uses `use_fast=True` because reliable
  offsets require `BertTokenizerFast`; OWLv2 uses `use_fast=False` to pin its
  saved slow native image processor. Loading is offline-only, pinned, safe-tensor
  only, and rejects remote code.
- Transformers 4.57.6 declares no SDPA implementation for OWLv2 and rejects
  `attn_implementation="sdpa"`. The bounded adapter therefore uses native eager
  attention in FP16 on CUDA; the experiment runner will measure peak VRAM and
  treat an over-budget model as infeasible instead of patching vendor attention.

## TDD and checks

- Initial RED: adapter module absent, `2 failed`.
- Mapping/coordinate/NMS/invalid-output RED: `8 failed`; GREEN after the bounded
  postprocessing implementation.
- Lifecycle RED: unload raised `NotImplementedError`; GREEN after real CPU move,
  reference release, collection, and CUDA cache release.
- Compatibility RED/GREEN: tests caught unsupported OWLv2 SDPA kwargs and the
  MM/OWLv2 processor-speed flag distinction before each correction.
- Current unit command: `.venv/bin/python -m pytest backend/tests/test_hf_open_detector.py -q`
  — `16 passed`.
- Current lint command: `.venv/bin/ruff check backend/src/pipe_inspector/vision/hf_open_detector.py backend/tests/test_hf_open_detector.py`
  — clean.

Real-weight GPU inference, latency, and peak VRAM remain runner-owned checks and
were intentionally not run in this unit task.

## GPU smoke follow-up

The first MM-Grounding-DINO GPU smoke exposed a native Transformers 4.57.6 dtype
conflict: the text enhancer receives an internally upcast FP32 query while its
FP16 attention weights remain half precision. MM now consistently loads its
model and moves all floating inputs as FP32 on both CUDA and CPU. OWLv2 remains
FP16 on CUDA and FP32 on CPU. The adapter exposes this effective choice as
`precision` for experiment pins.

Because Transformers ignores `revision=` for a local directory, loading now
checks each required model/config/processor/tokenizer asset and the relevant
family vocabulary files against `snapshot_download`'s adjacent metadata. The
metadata commit first line must match the pinned revision before Torch or
Transformers is imported; no content hashing or new asset ledger was added.

Follow-up RED covered MM CUDA precision and missing/mismatched local metadata.
The scoped GREEN command reports `19 passed`. Mean-token MM positive-map scoring
is unchanged pending the main agent's official-source review.
