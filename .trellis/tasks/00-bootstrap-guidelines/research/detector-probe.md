# M0/T02 detector compatibility probe

## Recommendation

- Add one top-level detector dependency: `ultralytics==8.4.46`.
- Keep the inherited runtime unchanged: Python 3.12.3, `torch==2.8.0+cu128`,
  `torchvision==0.23.0+cu128`. CUDA is available on the RTX 3080 Ti (12 GiB,
  compute capability 8.6); no separate CUDA toolkit or `torchaudio` is needed.
- Why this exact pin: static (non-unpickling) inspection of `best.pt` found the
  checkpoint's embedded Ultralytics version `8.4.46`, model YAML `yolo26s.yaml`,
  and only classes that exist in that release. Matching the writer minimizes
  pickle/class-layout compatibility risk.
- `ultralytics==8.4.46` is the smallest supported top-level declaration. Its
  official manifest supplies the dependency closure; do not separately promote
  transitive packages into project dependencies. The current environment already
  satisfies NumPy, Matplotlib, Pillow, PyYAML, Requests, torch, torchvision, and
  psutil; the resolver still needs OpenCV, SciPy, Polars, and ultralytics-thop.
- Official evidence: the [8.4.46 package manifest](https://github.com/ultralytics/ultralytics/blob/v8.4.46/pyproject.toml)
  supports Python >=3.8 and declares the runtime dependencies; PyTorch documents
  [`torch==2.8.0` + `torchvision==0.23.0` for CUDA 12.8](https://pytorch.org/get-started/previous-versions/);
  Ultralytics documents loading a YOLO26 `.pt` directly with `YOLO()` and calling
  it on an image in the [YOLO26 usage guide](https://docs.ultralytics.com/models/yolo26/).

## Checkpoint contract

- Path: `yolov26/experiments/sewer2403_5class_yolo26s_img960_e100/weights/best.pt`
- Task/model/input: detection, YOLO26s, `imgsz=960`; trained for 100 epochs.
- Class IDs: `0=PL` (破裂), `1=BX` (变形), `2=FS` (腐蚀),
  `3=BLOCK` (CJ 沉积 + JG 结垢 + ZW 障碍物), `4=AJ` (支管暗接).
- Post-install smoke command from repository root:

```bash
.venv/bin/yolo predict \
  model=yolov26/experiments/sewer2403_5class_yolo26s_img960_e100/weights/best.pt \
  source=var/fixtures/sewer2403-sample.jpg imgsz=960 device=0 save=False
```

## One real raw sample, not a dataset extraction

- Archive set is present: `sewer2403.z01`, `sewer2403.z02`, and
  `sewer2403.zip`. The selected member is physically readable from the final
  volume: a 42,012-byte 720x576 JPEG; its paired VOC XML contains one `AJ` box.
- Member: `市政下水雨污管道缺陷数据2403/images/Junction1286_jpg.rf.4aa21e765c22c320a59aa6f208c8f129.jpg`
- Reproducible single-member extraction (does not expand the archive):

```bash
mkdir -p var/fixtures
python -c 'from pathlib import Path; import zipfile; z=zipfile.ZipFile("yolov26/data_archives/sewer2403.zip"); Path("var/fixtures/sewer2403-sample.jpg").write_bytes(z.read("市政下水雨污管道缺陷数据2403/images/Junction1286_jpg.rf.4aa21e765c22c320a59aa6f208c8f129.jpg"))'
```

## Compatibility risks / verification boundary

- `best.pt` stores a pickled full `DetectionModel`, not a plain state dict.
  Ultralytics 8.4.46 intentionally loads it with `weights_only=False`
  ([loader source](https://github.com/ultralytics/ultralytics/blob/v8.4.46/ultralytics/nn/tasks.py#L1289-L1335));
  only load this trusted local checkpoint. PyTorch explains the pickle risk and
  its `weights_only` default in its [serialization notes](https://docs.pytorch.org/docs/stable/notes/serialization.html#torch-load-with-weights-only-true).
- The split ZIP reports itself as disk 3. Generic `unzip` warns and returns 1 even
  for the selected readable member; the Python command above was chosen for a
  clean, exact-member extraction. Earlier members require a split-aware tool.
- Training used the same torch/CUDA stack but an RTX 5090 32 GiB. Batch-1
  inference at 960 is the intended probe; actual latency and peak VRAM on the
  3080 Ti remain unmeasured until the pinned package is installed and the smoke
  command runs.
