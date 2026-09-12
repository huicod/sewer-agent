# Local enhancement model adapter report

## Scope and versions

- Baseline: Git `62783b472d24896cd558cd3295b7d580e3c908a9`; shared jj change
  `nnvxzzxz`. This worker did not mutate VCS state.
- Implemented only `vision/enhancement_models.py`, its `_enhancement_vendor/` package,
  `backend/tests/test_enhancement_models.py`, this report, and the explicitly allowed
  ignored model/smoke paths.
- Runtime used Python 3.12.3, Torch `2.8.0+cu128`, CUDA 12.8, Pillow 11.3.0, and
  `einops==0.8.1`. The latter was verified through the official PyPI 0.8.1 record and
  installed into `.venv` with `--no-deps`; Torch/CUDA were not changed.

## First-party sources and licensing

The vendored files preserve the license notices in
`backend/src/pipe_inspector/vision/_enhancement_vendor/LICENSES.md`.

| Model | Pinned architecture source | Official weight source | Repository code license |
|---|---|---|---|
| DehazeFormer-MCT | `https://huggingface.co/spaces/IDKiro/DehazeFormer_Demo/blob/8e0b26c7732e2b0dc104f69c5ea30f913c878a89/models/dehazeformer.py` and the pinned `models/__init__.py` MCT alias | `https://huggingface.co/spaces/IDKiro/DehazeFormer_Demo/resolve/8e0b26c7732e2b0dc104f69c5ea30f913c878a89/saved_models/dehazeformer.pth?download=true` | MIT, copyright 2023 IDKiro |
| Restormer motion deblurring | `https://github.com/swz30/Restormer/blob/68dc6ac472db26f16361150cb7a96a1bc87da93f/basicsr/models/archs/restormer_arch.py` | `https://github.com/swz30/Restormer/releases/download/v1.0/motion_deblurring.pth` | MIT, copyright 2022 Syed Waqas Zamir and contributors |
| Real-ESRGAN general x4v3 | `https://github.com/xinntao/Real-ESRGAN/blob/a4abfb2979a7bbff3f69f58f58ae324608821e27/realesrgan/archs/srvgg_arch.py` | `https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth` | BSD-3-Clause, copyright 2021 Xintao Wang |

These are repository/source-code license records. No separate explicit license metadata
for the weight binaries was found or is claimed here.

The downloaded local checkpoints were not overwritten if present:

- `var/models/enhancement/dehazeformer-mct-8e0b26c7.pth` — 5,927,945 bytes;
  expected key `state_dict`.
- `var/models/enhancement/restormer-motion-v1.0.pth` — 104,700,429 bytes;
  expected key `params`.
- `var/models/enhancement/realesr-general-x4v3-v0.2.5.0.pth` — 4,885,111 bytes;
  preferred key `params_ema`, fallback `params`.

No new hashing system was created. All three files strict-loaded with
`torch.load(weights_only=True, map_location="cpu")` against the vendored architectures:
1,233,024 / 26,126,644 / 1,213,296 parameters respectively.

## Necessary patches and adapter behavior

- DehazeFormer: reformatted/annotated the pinned architecture while retaining state-dict
  module paths. `MCT.get_coord` now creates `linspace` tensors with the input device and
  dtype, fixing the upstream CPU-coordinate/CUDA-input mismatch. The adapter executes its
  global curve mapping once over the whole supplied image/ROI in FP32, with no external
  tiling, and performs the official `[0,1] -> [-1,1] -> [0,1]` normalization.
- Restormer: removed only the unused debugger import and normalized formatting/identifiers
  while retaining state-dict paths and the official motion-deblurring constructor. The
  adapter reflect-pads to a multiple of eight when legal, uses replicate padding for tiny
  dimensions that cannot satisfy Torch reflect constraints, crops padding, and processes
  512-pixel tiles with 32-pixel overlap in FP32.
- Real-ESRGAN: removed the BasicSR registry import/decorator; retained the SRVGG body and
  state-dict paths with 64 features, 32 convolution blocks, PReLU, and x4 PixelShuffle.
  The adapter uses 256-pixel core tiles with ten input pixels of clipped context, then
  crops the scaled context before assembling the exact x4 result.
- Shared adapter: normalizes `str`/`StrEnum` kinds, loads lazily and locally, strict-loads
  only the approved named state dict, rejects missing/malformed weights, rejects non-PIL
  and over-1,048,576-pixel inputs before loading, requires one finite RGB tensor at the
  exact expected size, clamps finite model output into `[0,1]`, returns a new RGB PIL
  image, and exposes explicit `unload()` GPU cleanup. It never downloads, installs, or
  substitutes an interpolation/sharpening fallback during inference.

## TDD and static verification

Red evidence:

```text
.venv/bin/python -m pytest backend/tests/test_enhancement_models.py -q --tb=short
ERROR collecting ... ImportError: cannot import name 'enhancement_models'
```

The test file existed before the production adapter/vendor files. Its boundaries cover
metadata and `StrEnum`, lazy safe loading, named/strict state dicts and SR EMA preference,
missing/invalid weights, source immutability/RGB conversion, input pixel limits, malformed
or nonfinite outputs, unit-range clamping, exact output shape, deblur padding/tiling, SR
context tiling, and unload/reload lifecycle.

Green evidence after implementation and removal of the explicit-Pillow-mode deprecation:

```text
.venv/bin/python -m pytest backend/tests/test_enhancement_models.py -q --tb=short \
  -W error::DeprecationWarning
21 passed in 1.83s

.venv/bin/ruff check backend/src/pipe_inspector/vision/enhancement_models.py \
  backend/src/pipe_inspector/vision/_enhancement_vendor \
  backend/tests/test_enhancement_models.py
All checks passed!
```

Per the root handoff, no broader backend test loop was run by this worker; the root owns
integration and full-suite verification.

## Real serial GPU smoke

Evidence directory:
`var/evaluations/2026-09-12-enhancement-model-smoke/`; structured record:
`results.json`. Inputs are explicit center crops of
`var/evaluations/2026-09-11-confidence/paired/gallery/sample-0007/original.jpg`
(RGB 1920x1080). Timings include first checkpoint load plus inference. Models ran serially
on `cuda:0` (`NVIDIA GeForce RTX 3080 Ti`) and each was unloaded before the next model.

| Kind | Source ROI xyxy | Input -> RGB output | Elapsed | Peak allocated |
|---|---:|---:|---:|---:|
| dehaze | `[576,156,1344,924]` | 768x768 -> 768x768 | 0.837589 s | 219.338 MiB |
| deblur | `[768,348,1152,732]` | 384x384 -> 384x384 | 1.534669 s | 1,409.262 MiB |
| super_resolution | `[896,476,1024,604]` | 128x128 -> 512x512 | 0.057260 s | 25.958 MiB |

All saved input/output PNGs decode as RGB at those sizes and have finite uint8 ranges
covering 0..255. This proves local execution and engineering boundaries only; it does not
prove restored detail, detector gain, defect truth, or domain accuracy.

## Failures, unknowns, and handoff

- The first smoke-harness attempt failed before loading any model because this Torch build
  rejected a string device argument to `reset_peak_memory_stats`. Calling the accounting
  API for the current CUDA device fixed the harness; the adapter was unchanged. The full
  serial smoke then passed.
- No model inference, checkpoint, shape, finite-value, unload, or CUDA failure remained in
  the recorded smoke.
- Unknown/unevaluated: quality on the CCTV domain, seam visibility on large tiled inputs,
  peak memory at the maximum admitted dimensions, warm-model latency, and downstream
  detector/VLM effects. Those require separate evaluation and must not be inferred from
  these three crops.
- Rollback is removal of the added adapter/vendor/test/report files and the optional
  `.venv` einops package; the ignored model and smoke directories are independently
  removable. No original media or existing checkpoint was modified.
- Root owns whole-task integration, the full backend suite, independent review, task state,
  and human acceptance.
