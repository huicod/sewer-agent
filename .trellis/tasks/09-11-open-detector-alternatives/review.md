# Alternative detector integrated review

Reviewed `310a7320..aceab277549e5093b3501a12d8b8be3dd8a6b113` against the PRD,
model/revision notes, adapter report, and the named late CLI metadata edit. Review mode was
read-only except for this report.

## Critical

None.

## Important

1. **MM-Grounding-DINO does not use the declared native 0.30 score semantics.**
   [`backend/src/pipe_inspector/vision/hf_open_detector.py:197`](/root/autodl-tmp/server/backend/src/pipe_inspector/vision/hf_open_detector.py:197)
   averages sigmoid probabilities across every token in a concept and thresholds that mean at
   [`hf_open_detector.py:213`](/root/autodl-tmp/server/backend/src/pipe_inspector/vision/hf_open_detector.py:213).
   Transformers 4.57.6's native Grounding-DINO postprocessor instead scores each query by its
   maximum token probability before applying the box threshold. The real tokenizer produces six
   tokens for `lateral connection opening in sewer pipe` but only two or three for several other
   concepts, so this custom mean systematically penalizes longer concepts and can drop a native
   >0.30 detection or change its selected class. The unit test at
   [`backend/tests/test_hf_open_detector.py:258`](/root/autodl-tmp/server/backend/tests/test_hf_open_detector.py:258)
   locks in the custom behavior rather than checking the declared native behavior. Use the native
   query score/threshold while retaining deterministic token-span-to-concept mapping, then update
   the focused test before treating MM-GDINO results as a valid frozen-protocol comparison.

2. **The recorded model revision is not tied to the local files that are loaded.**
   [`backend/src/pipe_inspector/vision/hf_open_detector.py:114`](/root/autodl-tmp/server/backend/src/pipe_inspector/vision/hf_open_detector.py:114)
   passes a revision to `from_pretrained`, but all three loads receive the resolved local directory
   at lines 119, 122, and 139. Transformers resolves files directly from a local directory and
   ignores `revision`; an offline probe loaded both directories successfully even with
   `revision="not-the-declared-revision"`. Nevertheless, lines 60–61 and the saved evaluator pins
   identify the output as the hard-coded commit. A stale or substituted local snapshot would
   therefore be silently mislabeled and accepted on resume. Load the pinned repo/revision from the
   local HF cache, or validate a setup-produced commit provenance marker for the local directory;
   no model-file hashing system is required.

## Minor

None.

## Plan compliance and quality

- The comparison loader accepted the real cached inputs as exactly **48 image records** and
  rejected video inclusion; it reconstructs the accepted baseline/fusion records before reuse.
- OWLv2 uses the native grounded-object postprocessor with original `(height, width)`; its 4.57.6
  scaler correctly accounts for square padding. Both adapters use the immutable seven concepts,
  original RGB pixels, bounded same-concept stable NMS, `OTHER`, and at-most-100 output.
- Loading is lazy, local-only, safe-tensor-only, and rejects remote code. The actual local
  processors were confirmed as MM 800/1333 with `BertTokenizerFast` and OWLv2 1008 square with the
  slow `Owlv2ImageProcessor`; OWLv2 remains eager FP16 on CUDA rather than claiming SDPA support.
- `run_incremental(..., unload_after_sample=True)` preserves the old default; only the bounded
  alternative runner opts out and unloads once in `finally`. The changed-file set contains no VLM,
  primary-detector, fusion, configuration-default, worker, or production-wiring change, and the new
  adapter is referenced only by the offline comparison entry point.
- Galleries, checkpoints, summaries, review queues, and comparison output retain pending-review
  language and do not call added candidates confirmed gains. The late CLI metadata pins
  Transformers version, processor choice, precision, and score description, but correctly does not
  claim real inference success.

The supervising run reports fresh `.venv/bin/ruff check backend` success and
`692 passed, 84 skipped` for `backend/tests`; this review did not repeat that full suite. Targeted
offline probes validated the real processor/tokenizer selection, native sizes, caption spans, the
48-record reference, and the ineffective local-directory revision argument. Real-weight GPU
inference, latency, peak VRAM, and candidate evidence remain explicitly unverified pending public
weight downloads.

**Engineering readiness: with fixes.** No rollback is warranted, but fix the two Important
comparison-validity issues before running or interpreting the real MM-GDINO/OWLv2 experiment.

## Follow-up clarification — MM-Grounding-DINO scoring

Important finding 1 above is **withdrawn** after distinguishing the model-specific
MM-Grounding-DINO protocol from Transformers' generic Grounding-DINO convenience postprocessor.
OpenMMLab's MM-Grounding-DINO head converts token logits to class scores with a normalized
positive map: sigmoid probabilities over the known positive tokens for each class are averaged.
That is the behavior implemented at
[`backend/src/pipe_inspector/vision/hf_open_detector.py:197`](/root/autodl-tmp/server/backend/src/pipe_inspector/vision/hf_open_detector.py:197),
so unequal phrase token counts are normalized rather than an unintended length penalty. The
generic Transformers postprocessor's maximum-token box score is not the governing model-specific
classification protocol for this experiment.

This is not an implementation bug. The classification protocol is already explicit in the adapter
report and the late saved CLI configuration; the PRD's “declared threshold” wording alone was
ambiguous, but does not require a code change. The focused mean-token unit test is appropriate.
The updated readiness verdict is therefore **with one fix**: Important finding 2 (verifiable local
checkpoint revision provenance) remains open. The pending MM native-FP32 compatibility change and
real-weight GPU result are outside this historical diff review and must be reported from their own
verification evidence.

## Focused fix re-review — `aceab277..2508342`

Reviewed only the checkpoint-identity, precision, CLI-pin, documentation, and focused-test changes.
No new Critical, Important, or Minor findings were found in this fix range.

Important finding 2 is **resolved**. The adapter now enumerates the required single-file
Safetensors/config/processor/tokenizer assets for each selected repository and, before importing
Torch or Transformers, requires each asset's `snapshot_download` metadata first line to equal the
pinned commit at
[`backend/src/pipe_inspector/vision/hf_open_detector.py:205`](/root/autodl-tmp/server/backend/src/pipe_inspector/vision/hf_open_detector.py:205).
This correctly establishes the trusted download's version identity without claiming cryptographic
byte integrity or adding a prohibited hashing system. Missing assets, missing metadata, and a
mismatched commit fail closed; OWLv2's currently pending weight download therefore cannot be
mistaken for a runnable pinned checkpoint.

The MM FP16 compatibility failure is also handled consistently: the adapter declares MM as FP32
and OWLv2 CUDA as FP16 at
[`hf_open_detector.py:82`](/root/autodl-tmp/server/backend/src/pipe_inspector/vision/hf_open_detector.py:82),
uses that same value for model loading and floating inputs, and persists it from the detector in the
CLI configuration at
[`backend/src/pipe_inspector/evaluation/open_alternatives.py:201`](/root/autodl-tmp/server/backend/src/pipe_inspector/evaluation/open_alternatives.py:201).
The saved MM FP32 smoke independently demonstrates one successful 640x640 inference with three
candidates, 5.26 seconds cold time, 1738.36 MiB peak allocated, and 1962 MiB peak reserved. These
are smoke measurements, not cohort performance or confirmed accuracy gains.

The supervising run reports fresh Ruff success and `695 passed, 84 skipped`; this focused review
did not repeat the full suite. The withdrawn score finding remains withdrawn. The ongoing 48-image
MM run and pending OWLv2 download/run remain delivery evidence to be reported when complete.

**Updated engineering readiness: fix accepted; no code-review findings remain.** Final experimental
readiness still depends on completion and inspection of the authorized real-weight runs.
