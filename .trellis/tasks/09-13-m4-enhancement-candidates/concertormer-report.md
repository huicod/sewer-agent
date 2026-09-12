# Concertormer RealBlur-J candidate report

## Selection and provenance

- Candidate ID: `concertormer-realblur-j` (offline deblurring candidate only).
- Paper: Pin-Hung Kuo, Jinshan Pan, Shao-Yi Chien, and Ming-Hsuan Yang,
  “Efficient Concertormer for Image Deblurring and Beyond,” ICCV 2025,
  <https://openaccess.thecvf.com/content/ICCV2025/html/Kuo_Efficient_Concertormer_for_Image_Deblurring_and_Beyond_ICCV_2025_paper.html>.
- Official code: <https://github.com/setsunil/Concertormer>, pinned at commit
  `94269c556277805051644343e3a2cc76c4cd7aaf` (repository HEAD observed
  2026-09-13 UTC).
- Local official source checkout:
  `var/research/restoration-candidates/concertormer/upstream/`.
- Reproducible source snapshot:
  `var/research/restoration-candidates/concertormer/concertormer-official-94269c5.tar.gz`
  (93,683 bytes).
- The official README links the pretrained archive at
  <https://drive.google.com/file/d/1NvbGroZm4vVgvWJtmgW-6Fwd97PiukiL/view>.
  Its served filename is `CSA_models.zip`, size 1,298,912,519 bytes, last-modified
  2025-07-19. The ZIP directory identifies `models/RealBlur/RealJ.pth` as the
  checkpoint used by `options/test/RealBlur/RealJ.yml`.

The repository root `LICENSE` starts with the MIT license and copyright notice for
`megvii-model`, then carries the Apache-2.0 license and notice for BasicSR-derived
code. The vendored architecture uses the Concertormer architecture plus BasicSR's
small `LayerNorm2d` implementation, so both notices need to remain in the project
third-party license record. The official repository does not state a separate license
for pretrained checkpoint redistribution; the checkpoint is linked directly from its
official README, but that absence should not be represented as an explicit weight
license grant.

## Checkpoint provisioning

To avoid the other five unrelated models in the 1.30 GB archive, only the RealJ ZIP
entry was downloaded with HTTP byte ranges. The retained compressed entry is
`var/research/restoration-candidates/concertormer/RealJ.zip-entry` (187,744,420
bytes). Extraction checked the ZIP directory's uncompressed size and CRC32
`9f29175c`.

Provisioned checkpoint:

- path: `var/models/enhancement/concertormer-realblur-j.pth`
- size: 202,554,935 bytes
- safe load: `torch.load(..., map_location="cpu", weights_only=True)`
- payload: one `params` ordered state dictionary with 1,157 tensors
- architecture parameters: 50,535,664
- strict load: no missing or unexpected keys

The checkpoint and research artifacts are ignored local evidence, not Python package
data. No dependency was installed and no API was called.

## Vendored interface

`pipe_inspector.vision._enhancement_vendor.concertormer` exposes:

```python
Concertormer(...)
ConcertormerLocal(...)
build_concertormer() -> ConcertormerLocal
```

`build_concertormer()` exactly pins the official RealBlur-J test architecture:
width 48, expansion 2, encoder blocks `(6, 8, 24)`, middle blocks 2, decoder
blocks `(24, 8, 8)`, heads `(1, 2, 4) / 8 / (4, 2, 1)`, training size 256,
and local-grid factor 1.25.

Input is a batch-one RGB FP32 tensor in the same `[0, 1]` normalization used by
BasicSR's paired-image pipeline. Raw `forward` preserves upstream behavior and
returns four tensors; the full-resolution restoration is `outputs[-1]`. The caller
must clamp the final residual output for image encoding. The architecture's required
multiple is 64 (`2 ** (three encoder stages + log2(8-pixel CSA window))`). Its internal
padding retains upstream reflection for normal images, uses replication only where
reflection is illegal for tiny dimensions, and crops the final tensor back to its
input shape. If the caller pre-pads to 64, the internal pad is a no-op and the caller
can crop `outputs[-1]` to the original image.

## Verification

TDD coverage in `backend/tests/test_concertormer.py` checks:

- upstream list output and exact odd-size final restoration;
- legal padding and finite output for a 1x2 image;
- exact official RealBlur-J structure and parameter count;
- safe `params` extraction and strict official checkpoint loading.

A controlled numerical comparison loaded identical random state into the pinned
upstream implementation and the vendored implementation. Both the base architecture
and local-grid architecture produced identical intermediate and final tensors (max
absolute error `0.0`).

Full-image GPU smoke evidence is saved at
`var/research/restoration-candidates/concertormer/gpu-smoke-960x576.json`:

- hardware/runtime: NVIDIA GeForce RTX 3080 Ti, PyTorch 2.8.0+cu128, FP32;
- input: `[1, 3, 576, 960]`, representing the 960x540 design maximum padded to 64;
- raw output shapes: `[1,3,72,120]`, `[1,3,144,240]`, `[1,3,288,480]`,
  `[1,3,576,960]`; all finite;
- checkpoint load plus inference: 3.888 s; synchronized inference: 2.644 s;
- peak allocated: 4,973,736,448 bytes (4.63 GiB);
- peak reserved: 5,446,303,744 bytes (5.07 GiB).

This is comfortably below the 12 GiB constraint, so full-image inference is the
preferred bounded strategy at the design maximum. External 512-pixel tiling is not
needed and would introduce avoidable seam risk. The model's own local-grid attention
already bounds its high-resolution attention computation.

## Files changed by this subtask

- `backend/src/pipe_inspector/vision/_enhancement_vendor/concertormer.py`
- `backend/tests/test_concertormer.py`
- `.trellis/tasks/09-13-m4-enhancement-candidates/concertormer-report.md`

Shared vendor exports, candidate factory wiring, online defaults, and project license
registry updates are intentionally left to the root implementer.
