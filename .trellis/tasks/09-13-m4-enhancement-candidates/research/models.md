# T12-D restoration candidate research

Audit cutoff: 2026-09-13. This note selects one bounded, discriminative candidate
per task for a six-model Sewer2403 comparison. It does **not** claim universal SOTA
or authorize online selection/policy changes. Benchmark ranks vary with degradation,
training set, metric implementation, and test set.

## Shortlist

| Task | Deployed baseline | Candidate to test | Why this candidate |
|---|---|---|---|
| Motion deblur | Restormer motion | **Concertormer RealBlur-J** (`concertormer-realblur-j`) | ICCV 2025 model with an official RealBlur-J checkpoint; deterministic restoration, exact code/config available, and a closer JPEG/sRGB camera domain than a GoPro-only checkpoint. |
| Dehaze | DehazeFormer-MCT | **ConvIR-L Haze4K** (`convir-l-haze4k`) | 14.83M-parameter TPAMI 2024 convolutional model, official Haze4K checkpoint, MIT code, and substantially more capacity than the 1.23M baseline while remaining practical on a 12 GB GPU with tiling/padding. |
| ROI super-resolution x4 | Real-ESRGAN general x4v3 | **HAT-L x4 ImageNet-pretrained** (`hat-l-x4-imagenet`) | 40.8M-parameter CVPR 2023 distortion-oriented model with mature official code/weights. It is a useful fidelity counterpoint to the GAN-trained real-world baseline; the known bicubic-domain mismatch is part of the test. |

All three remain offline candidates until the same real Sewer2403 images, original
detections, enhanced detections, and human defect-fidelity review are complete.

## 1. Concertormer RealBlur-J deblurring

Primary sources: [ICCV 2025 paper](https://openaccess.thecvf.com/content/ICCV2025/html/Kuo_Efficient_Concertormer_for_Image_Deblurring_and_Beyond_ICCV_2025_paper.html),
[official repository](https://github.com/setsunil/Concertormer/tree/94269c556277805051644343e3a2cc76c4cd7aaf),
[pinned architecture](https://github.com/setsunil/Concertormer/blob/94269c556277805051644343e3a2cc76c4cd7aaf/basicsr/models/archs/Concertormer_arch.py),
and [RealBlur-J test config](https://github.com/setsunil/Concertormer/blob/94269c556277805051644343e3a2cc76c4cd7aaf/options/test/RealBlur/RealJ.yml).

- Exact weight source: the repository's [official `models.zip`](https://drive.google.com/file/d/1NvbGroZm4vVgvWJtmgW-6Fwd97PiukiL/view), inner file
  `models/RealBlur/RealJ.pth`. Download URL:
  `https://drive.usercontent.google.com/download?id=1NvbGroZm4vVgvWJtmgW-6Fwd97PiukiL&export=download&confirm=t`.
- Checkpoint key: **`params`**, confirmed against the downloaded official binary;
  strict load succeeds. See [Concertormer implementation evidence](../concertormer-report.md).
- Instantiate `ConcertormerLocal` exactly as the config: `width=48`,
  `ffn_expansion_factor=2`, `train_size=(1,3,256,256)`, `grid_factor=1.25`,
  encoder blocks `(6,8,24)`, middle `2`, decoder `(24,8,8)`, heads `(1,2,4) / 8 /
  (4,2,1)`.
- Input/output contract: RGB float32 in `[0,1]`, no mean/std transform. The network
  reflect-pads internally to a multiple of 64, returns three scale outputs, and the
  full-resolution result is the last element of a four-element list, cropped to the original size and
  clamped to `[0,1]`. The local-conversion constructor derives its attention base size
  from the official 256-pixel training size. Runtime-only architecture dependencies
  are Torch and the already-installed `einops`; the BasicSR registry and `LayerNorm2d`
  can be locally isolated instead of installing the full training stack.
- Official paper result: RealBlur-J **33.51 dB / 0.945 SSIM** and RealBlur-R
  **40.78 / 0.977**. Its GoPro result is reported as 0.21 dB above FFTformer. These
  are paired benchmark results, not sewer-domain evidence and not directly comparable
  with a differently trained deployed Restormer checkpoint.
- Licensing: the pinned repository `LICENSE` carries MIT terms for the NAFNet-derived
  portion and Apache-2.0 terms for BasicSR. The repository does not state a separate
  license for the checkpoint archive; redistribution/commercial use of weights needs
  a project-owner/legal decision rather than inference from the code license.

Why not the other deblur candidates now: [FFTformer](https://github.com/kkkls/fftformer/tree/01306b5d4072146a011c8c6e9d4927cf4607f1d5)
is an excellent MIT-licensed CVPR 2023 fallback with RealBlur weights and simpler
dependencies, but Concertormer is newer and reports stronger RealBlur-J fidelity.
LoFormer (ACM MM 2024) and SFHformer (ECCV 2024) have official checkpoints but do not
offer a better domain/accessibility tradeoff for this one-candidate test. The current
[DeepRFTv2 repository](https://github.com/INVOKERer/DeepRFTv2/tree/d49a66993d610a39591e1d501784f5223411354e)
is a 2025 preprint landing page without a documented downloadable checkpoint or license,
so it is not a reproducible replacement today. Diffusion deblurrers are excluded because
generated texture is a poor default for defect evidence and their dependency footprint
is unnecessary here.

## 2. ConvIR-L Haze4K dehazing

Primary sources: [official repository and reported results](https://github.com/c-yn/ConvIR/tree/05dcbffa2047fab79a7fe238fd52615ca31a33d5),
[pinned architecture](https://github.com/c-yn/ConvIR/blob/05dcbffa2047fab79a7fe238fd52615ca31a33d5/Dehazing/ITS/models/ConvIR.py),
[pinned layers](https://github.com/c-yn/ConvIR/blob/05dcbffa2047fab79a7fe238fd52615ca31a33d5/Dehazing/ITS/models/layers.py),
and [official inference code](https://github.com/c-yn/ConvIR/blob/05dcbffa2047fab79a7fe238fd52615ca31a33d5/Dehazing/ITS/eval.py).

- Exact official weight: `haze4k-large.pkl`, Google Drive file ID
  `1hQMT5nSl1X47wvbc84gh8PJ_8i_zMzAB`; [browser link](https://drive.google.com/file/d/1hQMT5nSl1X47wvbc84gh8PJ_8i_zMzAB/view) and
  download URL
  `https://drive.usercontent.google.com/download?id=1hQMT5nSl1X47wvbc84gh8PJ_8i_zMzAB&export=download&confirm=t`.
- Local provenance: downloaded without substitution as
  `var/models/enhancement/convir-l-haze4k-05dcbffa.pkl`, **59,688,013 bytes**.
  `torch.load(weights_only=True, map_location="cpu")` yields only key **`model`**,
  an `OrderedDict` of 794 tensors/buffers with **14,833,595 stored elements**.
  Root strict-load confirmed **14,830,985 model parameters**, matching the official
  14.83M ConvIR-L report. Tensor dtypes are float32 plus integer batch-norm
  counters. No hash was added per project policy.
- Instantiate `build_net("large", "Haze4k")`. This selects 16 residual blocks at each
  of three encoder/decoder stages with base width 32. Strict-load `payload["model"]`.
- Input/output contract: PIL RGB to float32 RGB `[0,1]`; reflect-pad right/bottom to a
  multiple of 32 as in the official evaluator; run `model(x)[2]`; crop to original
  dimensions and clamp to `[0,1]`. The inference architecture itself is Torch-only.
- Source caveat found during audit: the released evaluator calls `test_dataloader`
  without its required `data` argument, and `if data == 'ITS' or 'Densehaze' ...` in
  `layers.py` is a Python always-true condition. The latter makes every released
  dataset use dilation `(7,9,11)` and therefore fixes the checkpoint-compatible module
  shape/behavior. The vendored expression uses explicit membership, with the factory
  restricted to Haze4k: the selected `(7,9,11)` branch is unchanged. Strict loading
  and upstream numerical comparison passed. Reuse the architecture and normalization,
  not the broken CLI.
- Official repository result for ConvIR-L on Haze4K: **34.50 dB / 0.99 SSIM**. The same
  table reports ConvIR-S/B on several real-haze sets, sometimes with the smaller model
  outperforming base. This is explicit evidence that Haze4K synthetic ranking does not
  establish real-sewer ranking.
- Licensing: MIT, copyright 2024 Yuning Cui, for repository code. No separately stated
  checkpoint license was found; apply the same weight-redistribution caution as above.

Why not the other dehazers now: DEA-Net-CR (TIP 2024) has strong synthetic results but
the official repository exposes no top-level license, making reuse less clean.
MixDehazeNet (IJCNN 2024) is MIT and lighter but reports lower Haze4K/SOTS results than
the selected ConvIR family. CORUN (NeurIPS 2024) targets real-world haze, but its official
README still describes pretrained release as pending and adds an unfolding pipeline.
The 2026 UDPNet preprint adds Depth Anything V2 and depth-prior dependencies; it is not
a bounded, proven drop-in candidate. Generative/diffusion dehazing was excluded for
defect-evidence fidelity.

## 3. HAT-L x4 ImageNet-pretrained super-resolution

Primary sources: [CVPR 2023 paper](https://openaccess.thecvf.com/content/CVPR2023/html/Chen_Activating_More_Pixels_in_Image_Super-Resolution_Transformer_CVPR_2023_paper.html),
[official repository](https://github.com/XPixelGroup/HAT/tree/1638a9a822581657811867bf670717f8371fc3e5),
[pinned architecture](https://github.com/XPixelGroup/HAT/blob/1638a9a822581657811867bf670717f8371fc3e5/hat/archs/hat_arch.py),
and [exact HAT-L x4 config](https://github.com/XPixelGroup/HAT/blob/1638a9a822581657811867bf670717f8371fc3e5/options/test/HAT-L_SRx4_ImageNet-pretrain.yml).

- Exact weight: `HAT-L_SRx4_ImageNet-pretrain.pth`, Google Drive ID
  `1uefIctjoNE3Tg6GTzelesTTshVogQdUf`; [browser link](https://drive.google.com/file/d/1uefIctjoNE3Tg6GTzelesTTshVogQdUf/view) and
  download URL
  `https://drive.usercontent.google.com/download?id=1uefIctjoNE3Tg6GTzelesTTshVogQdUf&export=download&confirm=t`.
- Checkpoint key: **`params_ema`**, strict load.
- Exact constructor: `upscale=4`, `in_chans=3`, `img_size=64`, `window_size=16`,
  `compress_ratio=3`, `squeeze_factor=30`, `conv_scale=0.01`, `overlap_ratio=0.5`,
  `img_range=1`, twelve groups of depth 6, `embed_dim=180`, twelve entries of 6
  heads, `mlp_ratio=2`, `upsampler="pixelshuffle"`, `resi_connection="1conv"`.
- Input/output contract: RGB float32 `[0,1]`. Internally HAT applies
  `(x - (0.4488,0.4371,0.4040)) * 1.0`, then reverses it after reconstruction.
  Reflect-pad to a multiple of the 16-pixel window before inference, crop `4x` padding,
  and clamp the RGB result to `[0,1]`, matching the pinned
  [HATModel pre_process](https://github.com/XPixelGroup/HAT/blob/1638a9a822581657811867bf670717f8371fc3e5/hat/models/hat_model.py).
  The implementation keeps the shared tiny-image replicate fallback. Official tile
  mode exists, but measured 320-pixel ROI inference fits locally without tiling.
- The CVPR paper reports HAT-L x4 PSNR **28.60 on Urban100** and **33.09 on Manga109**,
  ahead of the paper's SwinIR results. Those are bicubic synthetic LR benchmarks with
  aligned ground truth. They neither measure blind CCTV degradation nor prove that
  added high-frequency content is a real crack, root, deposit, or joint edge.
- Licensing: Apache-2.0 repository code. The Drive weight has no separately stated
  license in the model zoo; weight redistribution remains an explicit review item.

Why not newer SR models in this first bounded pass: DRCT-L (CVPRW 2024), ATD
(CVPR 2024), and PFT (CVPR 2025) improve classical benchmark numbers, while NTIRE
2025/2026 results continue to move. Their gains are still measured mainly under
bicubic or challenge-specific degradations. PFT's official instructions require
patchwise inference even on an RTX 4090 and include a CUDA extension, increasing risk
without resolving the sewer-domain mismatch. HAT-L has a mature single architecture,
exact public checkpoint, strict key, pure-PyTorch inference path, and enough capacity
to make a meaningful fidelity comparison now. Do not switch to `Real_HAT_GAN_*` for
this test: the official project distinguishes GAN real-world/sharper models, whereas
the selected distortion checkpoint is the cleaner evidence-preservation probe.

## Comparison and promotion caveats

1. Freeze the six models and image list before looking at results; do not tune a model
   per image. Distinguish applicable degradation from explicitly labeled wrong-tool
   controls. This run deliberately forces every model offline, including controls;
   it does not simulate the online selector. Retain failure cases.
2. Preserve original pixels and compare, side by side: original, baseline output,
   candidate output, original detector result, both enhanced detector results, and
   human defect-fidelity labels. An enhanced-only detection is “new/unverified,” not
   automatically a recovered defect.
3. With no aligned clear Sewer2403 ground truth, PSNR/SSIM cannot be computed honestly
   on the real images. No-reference sharpness/contrast metrics can diagnose changes but
   cannot establish restoration or defect truth. A separately labeled downsample/blur/
   haze proxy set may report paired PSNR/SSIM, clearly separated from real-image review.
4. Reject promotion when enhancement erases a known defect boundary, creates a defect-
   like edge/texture, shifts geometry, introduces tile seams/ringing, or causes a worse
   reviewed detection outcome. Latency and peak memory are engineering constraints,
   secondary to defect fidelity.
5. Keep all inference offline/local, preserve originals, and leave default/automatic
   online behavior unchanged. Candidate success on this bounded source-domain sample
   is not independent generalization evidence because Sewer2403 split provenance is
   incomplete.

## Implementation outcome (2026-09-13)

All three strict-load and real CUDA smoke checks passed. HAT-L has 40,846,575
parameters; its local official weight is `hat-l-x4-imagenet-1638a9a8.pth`
(165,774,123 bytes). No training-stack dependency was installed. CPU comparisons
with identical random weights against pinned upstream HAT/ConvIR small architectures
gave maximum absolute error 0.0; Concertormer evidence is linked above.

The fixed eight-image run completed, but no candidate is promoted online. In
particular, ConvIR's dark sample-0025 output is severely corrupted despite finite
tensors. A direct official-code / official-weight rerun reproduces the corruption
(99.258% of raw values outside `[0,1]`, saved image maximum pixel difference 1).
This is a visual failure, not a valid dehazing benefit. Exact comparison, caveats,
timing, and gallery are in the [stage report](../report.md). Human domain acceptance
remains distinct from the agent's visual inspection.
