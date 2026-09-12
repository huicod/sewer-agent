# T13-A model choice: prompted mask + relative depth

Checked 2026-09-13 against official repositories/model cards. This is a practical
choice for one original Sewer2403 CCTV image at a time on an RTX 3080 Ti 12 GiB;
it is not a claim that either model is universally best on sewer imagery.

## Selected pair

| Tool | Exact official model | Immutable HF revision / weight | License | Why this one |
|---|---|---|---|---|
| Box-prompt mask | `facebook/sam2.1-hiera-large` | `665f8e2ad61cf5f53d65644ff27c8ee525124610`, `model.safetensors`, 897,897,416 B (856.3 MiB) | Apache-2.0 | Highest-capacity SAM 2.1 checkpoint (224.4M parameters), public/no auth, native in installed Transformers 4.57.6, and comfortably plausible for one FP16 image on 12 GiB. |
| Monocular relative depth | `depth-anything/Depth-Anything-V2-Small-hf` | `5426e4f0f36572d16453bbda7a8389317b1bef99`, `model.safetensors`, 99,173,660 B (94.6 MiB) | Apache-2.0 | Public/no auth, 24.8M parameters, native in installed Transformers 4.57.6, and avoids the non-commercial V2 Base/Large weights and DA3's extra runtime stack. |

Pinned downloads (provision once; runtime must load the local directory with
`local_files_only=True`, never silently fetch):

- [SAM 2.1 snapshot](https://huggingface.co/facebook/sam2.1-hiera-large/tree/665f8e2ad61cf5f53d65644ff27c8ee525124610) and [safetensors file](https://huggingface.co/facebook/sam2.1-hiera-large/resolve/665f8e2ad61cf5f53d65644ff27c8ee525124610/model.safetensors)
- [Depth Anything V2 Small snapshot](https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf/tree/5426e4f0f36572d16453bbda7a8389317b1bef99) and [safetensors file](https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf/resolve/5426e4f0f36572d16453bbda7a8389317b1bef99/model.safetensors)

Provision the complete small metadata snapshot as well as the named weight, e.g.:

```bash
hf download facebook/sam2.1-hiera-large \
  --revision 665f8e2ad61cf5f53d65644ff27c8ee525124610 \
  --local-dir var/models/geometry/sam2.1-hiera-large \
  --include '*.json' 'model.safetensors'
hf download depth-anything/Depth-Anything-V2-Small-hf \
  --revision 5426e4f0f36572d16453bbda7a8389317b1bef99 \
  --local-dir var/models/geometry/depth-anything-v2-small \
  --include '*.json' 'model.safetensors'
```

## Exact inference conventions

### SAM 2.1 Hiera Large

- Implementation correction after reading the pinned config: load
  `AutoModelForMaskGeneration` (`model_type=sam2_video`) plus `AutoProcessor`
  from the local snapshot, in eval mode,
  `dtype=torch.float32`; use `torch.inference_mode()` and one model resident.
- Input is the unmodified original RGB image. OpenCV input is BGR, so convert
  with `cv2.COLOR_BGR2RGB`; PIL should be `.convert("RGB")`.
- Supply exactly one original-image pixel box as
  `input_boxes=[[[x1, y1, x2, y2]]]` (XYXY, top-left then bottom-right).
  Do not crop the image and do not invoke an automatic full-image mask pipeline.
- Pinned processor behavior: bilinear resize to `1024 x 1024` (aspect ratio is
  stretched internally), RGB conversion, `/255`, ImageNet mean/std, no padding.
  The processor rescales XY independently; retain its `original_sizes`.
- Call the model with `multimask_output=False`. `pred_masks` are low-resolution
  logits shaped `[batch, boxes, 1, h, w]`; `iou_scores` are model mask-quality
  estimates, not defect confidence or accuracy.
- `processor.post_process_masks(pred_masks, original_sizes, mask_threshold=0.0,
  binarize=True)[0][0, 0]` yields the original `H x W` boolean mask. Do not clip
  the returned mask to the prompt box: SAM masks may legitimately cross it.

### Depth Anything V2 Small

- Load `AutoImageProcessor` and `AutoModelForDepthEstimation` from the local
  snapshot, eval mode, FP32 inference with `use_fast=False`. Give it the same original RGB image;
  explicitly convert OpenCV BGR to RGB.
- Pinned preprocessing is bicubic, aspect-preserving toward `518 x 518`, with
  both dimensions rounded to a multiple of 14 (normally the short side is near
  518), `/255`, ImageNet mean/std, and **no padding**. Wide images therefore use
  more than 518 pixels on the long side.
- `outputs.predicted_depth` is `[B,h,w]`. Restore the source raster with
  `processor.post_process_depth_estimation(outputs,
  target_sizes=[(H, W)])[0]["predicted_depth"]` (bicubic) and save float32.
- Direction contract: V2 is a disparity/inverse-depth-style relative model;
  **larger raw value = nearer**, smaller = farther. This direction follows the
  official DA3 card's explicit contrast (“DA3 directly predicts depth, unlike
  disparity-based Depth Anything 2”) and V2's positive ReLU head. It is an
  ordering only: arbitrary scale/shift, no metres, no cross-image comparison.
- Normalize per image only for display (`min -> black/far`, `max -> white/near`).
  Reject non-finite or constant maps rather than inventing a scale.

## Resource estimate and operating caveats

Implementation uses FP32 for both tools; the estimates below were pre-download
planning only. Actual successful FP32 smokes and six-image results are recorded
in [model-report.md](../model-report.md) and [report.md](../report.md).

- **Measured artifact sizes:** 856.3 MiB SAM and 94.6 MiB depth safetensors.
- **Planning estimate, not benchmarked here:** FP16 parameter storage is about
  428 MiB and 47 MiB respectively; expected peak is roughly 2–4 GiB for SAM at
  1024 square and below roughly 1.5 GiB for ordinary CCTV aspect ratios with V2
  Small. The 12 GiB card should have ample margin when models are loaded/run/
  unloaded serially. Record real peak VRAM and latency in the required GPU run.
- Zero-shot quality is unverified on Sewer2403. Darkness, fisheye distortion,
  blur, specular wet walls and water surfaces can confuse both tools; a box can
  select pipe/water rather than the defect. The frozen 4–6-frame review is the
  acceptance evidence; prompt boxes are not mask ground truth.

## Why not the newer alternatives now

- **SAM 3 / current SAM 3.1:** official `facebook/sam3` and
  `facebook/sam3.1` are gated (login, contact sharing and accepted access), use
  Meta's custom SAM License, and carry about 3.44 GB / 3.50 GB weights. Their
  text/concept segmentation is also outside this box-only task. SAM 2.1 is the
  reproducible no-auth boundary, not an assertion that it is more accurate.
- **Depth Anything 3:** `depth-anything/DA3MONO-LARGE` is the appropriate DA3
  relative single-view variant (0.35B, Apache-2.0, public safetensors about
  1.34 GB), but Transformers 4.57.6 does not implement it. The official package
  brings `xformers`, `open3d`, `pycolmap`, `e3nn`, `trimesh` and other geometry/
  service dependencies. Gate it to a later isolated Sewer2403 benchmark.
- **Depth Pro:** official `apple/DepthPro-hf` is a roughly 1.9 GB metric-depth
  model with focal-length estimation. Metric output is expressly out of scope;
  normalizing it would add cost without serving this relative-only contract.

## Primary sources

- Meta [SAM 2 README/model table/API](https://github.com/facebookresearch/sam2/blob/main/README.md), [Apache-2.0 license](https://github.com/facebookresearch/sam2/blob/main/LICENSE), and [official direct `.pt` URL](https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_large.pt)
- Meta [SAM 3 repository/license](https://github.com/facebookresearch/sam3) and [SAM 3.1 release note](https://github.com/facebookresearch/sam3/blob/main/RELEASE_SAM3p1.md)
- Depth Anything [V2 README, model sizes and per-weight licenses](https://github.com/DepthAnything/Depth-Anything-V2/blob/main/README.md), [official inference/preprocessing](https://github.com/DepthAnything/Depth-Anything-V2/blob/main/depth_anything_v2/dpt.py), and [HF model card](https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf)
- ByteDance Seed [DA3 model table and licenses](https://github.com/ByteDance-Seed/Depth-Anything-3/blob/main/README.md), [official dependency manifest](https://github.com/ByteDance-Seed/Depth-Anything-3/blob/main/pyproject.toml), and [DA3MONO-LARGE card](https://huggingface.co/depth-anything/DA3MONO-LARGE)
- Apple [Depth Pro reference/model contract](https://github.com/apple/ml-depth-pro/blob/main/README.md) and [HF Transformers checkpoint](https://huggingface.co/apple/DepthPro-hf)
