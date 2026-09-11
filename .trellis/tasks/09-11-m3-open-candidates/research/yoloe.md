# YOLOE feasibility on the installed runtime

## Recommendation

Use the official text/visual-prompt checkpoint **`yoloe-26s-seg.pt`** as the first adapter candidate:

- exact weight URL: <https://github.com/ultralytics/assets/releases/download/v8.4.0/yoloe-26s-seg.pt>
- source trust: this URL is linked by the [official Ultralytics YOLOE model table](https://docs.ultralytics.com/models/yoloe/#available-models), is hosted in the official `ultralytics/assets` GitHub release, and matches the installed downloader's `v8.4.0` default (`ultralytics/utils/downloads.py:462-512`)
- scale choice: the official guide recommends 26s as the starting model; its reported LVIS text-prompt mAP is 30.8 versus 24.7 for 26n, while 26s remains small (10.7 M detection-configuration parameters). Use 26n only as the memory/latency fallback.

This is feasible in principle on the local NVIDIA RTX 3080 Ti (12,288 MiB). Ultralytics says YOLOE inference needs 4–8 GB and that `n`/`s` are the small deployment scales. The installed `ultralytics==8.4.46` is newer than the official YOLOE-26 minimum, 8.4.0. No Ultralytics, PyTorch, or torchvision upgrade is indicated.

Use **text prompts**, limited to sewer-defect concepts. Do not use `yoloe-26s-seg-pf.pt`: prompt-free mode emits from its built-in 4,585-name vocabulary, so ordinary objects would enter the candidate stream, and the installed implementation raises the exact assertion below if callers try to restrict it with `set_classes()`:

```text
Prompt-free model does not support setting classes. Please try with Text/Visual prompt models.
```

The five canonical defect concepts plus explicitly reviewed open concepts such as `root intrusion` and `water infiltration` are appropriate inputs. The adapter must preserve caller-owned mapping: the five known concepts map to their canonical labels; out-of-taxonomy concepts become named `OTHER` candidates requiring human review. A generic prompt-free label must never be translated automatically to `OTHER`.

## Installed API and exact call shape

All released YOLOE checkpoints are instance-segmentation weights. Load them explicitly as `task="segment"`; detection consumers use the boxes already present in each segmentation `Results` object and may ignore masks.

```python
from pathlib import Path

import numpy as np
from ultralytics import YOLOE

weights = Path("models/yoloe-26s-seg.pt")
prompt_names = [
    # Caller/config supplies the agreed English defect descriptions.
    # Keep output-name-to-canonical/open-label mapping alongside this list.
]

# Loading starts on CPU. Calling set_classes before predict keeps the transient
# text encoder off the GPU; the embeddings are reused for subsequent frames.
model = YOLOE(weights, task="segment")
model.set_classes(prompt_names)

# frame is one HWC uint8 BGR ndarray, matching the existing OpenCV pipeline.
results = model.predict(
    source=frame,
    device=0,
    imgsz=640,
    conf=0.10,       # provisional smoke value, not a production threshold
    half=False,      # establish a correctness baseline before trying FP16
    verbose=False,
)

result = results[0].cpu()
boxes = result.boxes
if boxes is not None:
    xyxy: np.ndarray = boxes.xyxy.numpy()
    scores: np.ndarray = boxes.conf.numpy()
    class_ids: np.ndarray = boxes.cls.int().numpy()
    names = [result.names[int(class_id)] for class_id in class_ids]
else:
    xyxy = np.empty((0, 4), dtype=np.float32)
    scores = np.empty((0,), dtype=np.float32)
    class_ids = np.empty((0,), dtype=np.int64)
    names = []
```

Relevant installed code:

- `ultralytics/models/yolo/model.py:192-252` maps both detect and segment tasks; segment uses `YOLOESegModel` and `SegmentationPredictor`.
- `ultralytics/models/yolo/model.py:306-323` implements `set_classes()` and generates text embeddings when none are supplied.
- `ultralytics/models/yolo/model.py:351-431` delegates ordinary prediction after forcing class-agnostic NMS for YOLOE.
- `ultralytics/engine/results.py:825-927` exposes `boxes.xyxy`, `boxes.conf`, and `boxes.cls`; `Results.cpu()` is supported.
- The matching official API is documented at <https://docs.ultralytics.com/reference/models/yolo/model/#ultralytics.models.yolo.model.YOLOE>.

`set_classes()` should run once per configured prompt set, not once per frame. Its class names become `result.names`. Because YOLOE forces class-agnostic NMS, overlapping aliases can suppress one another; start with one prompt per intended output concept and evaluate aliases separately.

## Text encoder and cache state

Text-prompt prediction has an extra first-use dependency that is **not** declared in the base Ultralytics package metadata:

1. Importing `ultralytics.nn.text_model` tries `import clip`; on failure, installed code calls `check_requirements("git+https://github.com/ultralytics/CLIP.git")` (`ultralytics/nn/text_model.py:15-19`). This may run pip and therefore must not be allowed as an accidental request-time side effect.
2. YOLOE-26 declares `text_model: mobileclip2:b` (`ultralytics/cfg/models/26/yoloe-26-seg.yaml:8-15`). `build_text_model()` maps that to `MobileCLIPTS(..., weight="mobileclip2_b.ts")` (`ultralytics/nn/text_model.py:338-358`).
3. The text encoder is fetched through `attempt_download_asset()` and loaded with `torch.jit.load()` (`ultralytics/nn/text_model.py:279-335`). The official asset is <https://github.com/ultralytics/assets/releases/download/v8.4.0/mobileclip2_b.ts>; the official docs describe it as about 254 MB and state that it is stored in the process current working directory.

Observed without importing or loading a model:

```text
ultralytics==8.4.46
torch==2.8.0+cu128
torchvision==0.23.0+cu128
clip: absent
mobileclip: absent (not needed by the TorchScript path)
transformers/open_clip_torch: absent (not required by this path)
yoloe-26s-seg.pt: absent in repository
mobileclip2_b.ts: absent in repository
```

Therefore the candidate is **code-compatible but not offline-ready**. The approved smoke must pre-provision the official checkpoint, the Ultralytics CLIP tokenizer package, and `mobileclip2_b.ts`, or explicitly authorize their controlled first-use installation/download. The installed code names a floating Git dependency rather than a pinned CLIP commit; capture the resolved revision in the eventual environment lock after the smoke. No paid API or training is involved.

## Unload and failure behavior

Ultralytics 8.4.46 has no public `unload()` method. Return CPU-owned results, remove predictor/model references, then clear only PyTorch's unused CUDA cache:

```python
import gc
import torch

cpu_results = [result.cpu() for result in results]
del results

# Predictor retains a reference to the torch model after first prediction.
model.predictor = None
model.cpu()
del model
gc.collect()
torch.cuda.empty_cache()
```

`empty_cache()` does not free live tensors: the owning adapter, any streaming generator, and GPU `Results` references must also be closed/deleted. `Model.cpu()` resets the predictor in this installed version (`ultralytics/engine/model.py:849-874`).

Expected failures and bounded fallbacks:

- CLIP install or asset download denied/offline: fail adapter initialization clearly; do not retry per frame. Pre-provision the two dependencies. If dependencies cannot be approved, use a visual prompt on the same `*-seg.pt` checkpoint and `YOLOEVPSegPredictor`; visual prompting needs an annotated reference box but no text encoder.
- `set_classes()` assertion mentioning prompt-free: the wrong `*-seg-pf.pt` asset was configured; switch to `yoloe-26s-seg.pt`.
- CUDA OOM: retry the controlled smoke with `yoloe-26n-seg.pt`, then reduce `imgsz`; do not upgrade core packages. Consider `half=True` only after an FP32 result is established.
- Valid execution but no sewer detections: treat this as a zero-shot/domain result, not an integration failure. Do not lower the threshold into an unbounded stream or auto-confirm `OTHER`; evaluate on labeled positive and hard-negative sewer frames.

## Required smoke (not run in this research task)

The actual local smoke needs explicit approval for network/dependency mutation and should run from a fixed repository working directory so the encoder cache location is deterministic. It should use:

1. the exact official 26s weight URL and encoder asset above;
2. the existing `.venv` unchanged except for the approved Ultralytics CLIP dependency;
3. one raw positive sewer frame and one hard negative/normal frame (the repository currently exposes training/validation mosaic artifacts, not a clean raw acceptance pair);
4. one short defect-only prompt list, `task="segment"`, CUDA device 0, `imgsz=640`, FP32, and a low provisional confidence;
5. assertions that output is a list of `Results`, boxes have finite `(N, 4)` coordinates, class IDs resolve only to configured prompt names, masks do not break box extraction, and unload returns allocated GPU memory to baseline within normal allocator tolerance;
6. a second prediction on the same instance to prove the text encoder is not re-downloaded or rebuilt per frame, plus one empty-detection path.

The smoke establishes runtime compatibility and memory behavior only. Candidate usefulness requires separate labeled sewer-frame review because zero-shot quality for these domain-specific defects is not guaranteed by the general LVIS metrics.
