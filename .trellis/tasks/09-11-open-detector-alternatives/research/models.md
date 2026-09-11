# Open-vocabulary detector shortlist (2026-09-11)

## Decision

Evaluate only these two first, with the existing VLM and frozen local dataset unchanged:

1. **MM-Grounding-DINO Base** — `openmmlab-community/mm_grounding_dino_base_o365v1_goldg_v3det`, revision `6f52a61753b22842954209e4b190b22135c57bf0`.
2. **OWLv2 L/14 ST/FT ensemble** — `google/owlv2-large-patch14-ensemble`, revision `95e26936e865f87db1742128404b3c035d47d89d`.

Both Hub APIs reported `private=false`, `gated=false`; both provide Safetensors and Apache-2.0 model-card licenses. Pin revisions so a later Hub update cannot silently change the comparison. Neither needs training or vendor credentials.

## 1. MM-Grounding-DINO Base (first choice)

- [Pinned HF repository](https://huggingface.co/openmmlab-community/mm_grounding_dino_base_o365v1_goldg_v3det/tree/6f52a61753b22842954209e4b190b22135c57bf0); 233,473,940 parameters and a 936,057,092-byte F32 Safetensors file.
- The [official OpenMMLab model table](https://github.com/open-mmlab/mmdetection/blob/main/configs/mm_grounding_dino/README.md#zero-shot-coco-results-and-models) reports **52.5 COCO box mAP** for this exact Swin-B variant, trained on Objects365 v1 + GoldG + V3Det. COCO is not listed in its training data, so this is the cleanest published COCO-transfer number in the shortlist.
- It improves Grounding DINO's contrastive class head and stops sharing decoder parameters. It keeps phrase-grounding semantics and supports `AutoProcessor` + `AutoModelForZeroShotObjectDetection`; [Transformers v4.56 docs](https://huggingface.co/docs/transformers/v4.56.0/en/model_doc/mm-grounding-dino) establish native support, so the isolated 4.57.6 environment is compatible.
- Do **not** attach the card's generic 31.9 LVIS-val / 41.4 LVIS-minival claims to this base checkpoint: the official LVIS table publishes those results for the Tiny O365+GoldG+GRIT+V3Det variant, not Base.
- Inference fit estimate (not an official measurement): FP16 weights are about 0.47 GB; batch 1 at the default 800-short/1333-long resize should plausibly peak around **4–8 GB VRAM**, leaving workable headroom on a 12 GB RTX 3080 Ti. Measure peak allocation locally; prompt length, image aspect, kernels, and allocator state matter.

## 2. OWLv2 Large Patch-14 Ensemble (diversity check)

- [Pinned HF repository](https://huggingface.co/google/owlv2-large-patch14-ensemble/tree/95e26936e865f87db1742128404b3c035d47d89d); 437,610,759 parameters and a 1,750,520,144-byte F32 Safetensors file; direct Transformers `Owlv2Processor`/`Owlv2ForObjectDetection` support.
- This is a **weight-space interpolation**, not two models run at inference. The [official Scenic checkpoint table](https://github.com/google-research/scenic/tree/main/scenic/projects/owl_vit#pretrained-checkpoints) identifies the L/14 ST/FT ensemble (`...st-ngrams-ft-lvisbase-ens-cold-weight-04...`) and reports **44.6 LVIS-val AP / 42.6 APr** with its evaluator. The repository warns these can differ slightly from paper values.
- Protocol/training: OWL-ST self-trains on WebLI pseudo-boxes, then fine-tunes on `LVIS_base`; the ensemble averages pre/post-fine-tuning weights. The [paper](https://ar5iv.labs.arxiv.org/html/2306.09683#S4.SS6) says no human boxes for LVIS rare classes were used, but also warns rare-class AP is a narrow open-vocabulary measure and fine-tuning can reduce broader OOD robustness. WebLI was deduplicated against common benchmarks, not ODinW.
- Its CLIP-style late fusion and fixed 1008x1008 L/14 vision encoder provide a useful failure-mode contrast to MM-GDINO. The official repository also notes v2's aggressive mosaics/size augmentation improve small-object detection.
- FP16 weights are about 0.88 GB; 5,184 vision tokens make attention memory material. Local source inspection found native Transformers 4.57.6 OWLv2 **does not support SDPA**. Use native eager attention, FP16 and batch 1, measuring actual peak allocation before a cohort run. No custom attention patch or speculative VRAM claim.

## Grounding-DINO number correction

`IDEA-Research/grounding-dino-base` revision `12bdfa3120f3e7ec7b434d90674b3396eccf88eb` is a Transformers conversion of `groundingdino_swinb_cogcoor.pth` (see the [official conversion mapping](https://github.com/huggingface/transformers/blob/main/src/transformers/models/grounding_dino/convert_grounding_dino_to_hf.py)). The [official checkpoint table](https://github.com/IDEA-Research/GroundingDINO#checkpoints) assigns that Swin-B checkpoint **56.7 COCO AP** and lists COCO among its training data. It is therefore not COCO-zero-shot under the paper's definition.

The HF card's **52.5 zero-shot AP** sentence instead repeats the [paper's Swin-L result](https://ar5iv.labs.arxiv.org/html/2303.05499#S4.SS2.SSS1): Swin-L trained on O365 + OpenImages + GoldG, evaluated on COCO 2017 val without COCO training images. It is not the metric of the HF `grounding-dino-base` weights. Keep the older Base checkpoint only as a mature compatibility fallback, not as one of the first two runs.

## Interpretation boundary

COCO/LVIS AP averages ranked detections across classes and IoU thresholds; it is neither per-frame confidence calibration nor sewer precision/recall. Dataset overlap can inflate apparent transfer, and neither source reports this project's classes, optics, lighting, or failure costs. These numbers justify candidates only; choose the detector by the frozen sewer evaluation, including per-class precision/recall, false-positive review, missed-object review, latency, and measured peak VRAM.
