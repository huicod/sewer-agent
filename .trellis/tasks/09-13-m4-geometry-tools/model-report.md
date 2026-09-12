# T13-A geometry model implementation report

## Reproducible local models

Both adapters use one model at a time in FP32 on `cuda:0`. Runtime loading is
lazy and strictly local: `local_files_only=True`, `trust_remote_code=False`,
`use_safetensors=True`, exact config `model_type`, and empty Transformers weight
loading diagnostics are required. No credentials or runtime downloads are used.

| Adapter | Official checkpoint | Pinned revision | Safetensors size |
|---|---|---|---:|
| Box-prompt mask | `facebook/sam2.1-hiera-large` | `665f8e2ad61cf5f53d65644ff27c8ee525124610` | 897,897,416 B |
| Relative depth | `depth-anything/Depth-Anything-V2-Small-hf` | `5426e4f0f36572d16453bbda7a8389317b1bef99` | 99,173,660 B |

Local directories are `var/models/geometry/sam2.1-hiera-large` and
`var/models/geometry/depth-anything-v2-small`. Each includes a small explicit
`provenance.json` containing only the exact `repo_id` and revision. Required
config/processor JSON files and `model.safetensors` are checked before library
imports. Files came from the official immutable Hugging Face revisions:

- [SAM 2.1 Hiera Large snapshot](https://huggingface.co/facebook/sam2.1-hiera-large/tree/665f8e2ad61cf5f53d65644ff27c8ee525124610)
- [Depth Anything V2 Small snapshot](https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf/tree/5426e4f0f36572d16453bbda7a8389317b1bef99)

The large safetensors files were fetched from the corresponding official
revision-pinned `resolve/.../model.safetensors` URLs without authentication.
Package-private Hugging Face cache metadata is not required at runtime.

## Adapter behavior

- `LocalSegmenter` loads the official SAM config (`model_type=sam2_video`) via
  `AutoModelForMaskGeneration`. It accepts one PIL RGB original and one original
  XYXY pixel box, passes `input_boxes=[[[x1,y1,x2,y2]]]`, requests one mask, and
  restores an original-size boolean raster at logit threshold zero. The mask is
  not cropped to the prompt. Its score is SAM's predicted mask IoU, not defect
  confidence.
- `LocalRelativeDepth` pins the slow DPT image processor (`use_fast=False`) and
  restores one original-size `float32` raster. `larger_is="nearer"`: values are
  relative inverse-depth/disparity ordering only, with no metres or cross-image
  scale. A pre-forward projection rejects DPT processed rasters over 2,097,152
  pixels, including pathological aspect ratios, before loading the model.
- Both adapters bind model state before device transfer so `unload()` can recover
  a failed transfer. `unload()` drops all adapter references, moves the model to
  CPU when possible, collects garbage, and empties the CUDA allocator cache.

## Real sample-0022 smoke evidence

Input was the frozen original RGB image, 720x576, and its frozen original YOLO
AJ box `[280.3342, 66.4887, 549.1299, 350.4391]`. Times include lazy local load
plus one forward; CUDA peak is `torch.cuda.max_memory_allocated()`. Models were
run serially on the RTX 3080 Ti 12 GiB.

| Model | Restored output | Result | Time | Peak CUDA allocated |
|---|---|---|---:|---:|
| SAM 2.1 Hiera Large FP32 | bool 576x720 | 22,375 true px; score 0.711266 | 3.946 s | 1,355,755,520 B (1292.95 MiB) |
| Depth Anything V2 Small FP32 | float32 576x720 | finite; min 0.521978, max 4.471088 | 3.315 s | 245,281,792 B (233.92 MiB) |

FP32 fits with large margin, so no FP16 reliability/compatibility tradeoff is
needed. Immediately after adapter unload, the live Python processes retained
75,628,544 B (SAM) and 8,519,680 B (depth) reported by the CUDA tensor allocator;
their precise ownership was not established in this smoke. Process exit released
the GPU completely. Final `nvidia-smi` showed no compute
process and 1 MiB used, so the GPU was handed back for frozen-six integration.

The first SAM load exposed an integration mismatch before any model forward:
the official checkpoint config is `sam2_video`, not `sam2`. Inspection of the
installed Transformers 4.57.6 auto mapping established
`AutoModelForMaskGeneration` as the supported loader. A regression test was
added, then the single successful SAM forward above was run. Depth's explicit
`use_fast=False` preserves the processor path used by its successful smoke.

## Verification and limits

- Adapter boundary suite: `19 passed`.
- Ruff on the adapter and its tests: `All checks passed!`.
- Tests cover lazy loading, exact offline/safetensors/FP32 arguments, strict
  loading diagnostics, provenance/assets/model type, original coordinate and
  raster conventions, depth aspect cap, malformed/non-finite outputs, device
  transfer cleanup, unload/reload, and RGB validation.

This is one execution smoke, not an accuracy benchmark or sewer-domain quality
claim. The prompt is a detector box, not mask ground truth. Wet surfaces,
darkness, blur, distortion, and reflections remain qualitative risks; the
frozen-six evaluator is the acceptance evidence. Depth output remains relative
even when its numeric range appears stable.
