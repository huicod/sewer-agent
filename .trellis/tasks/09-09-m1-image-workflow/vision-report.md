# T05 vision and rule report

## Delivered scope

- `contracts/inspection.py`: frozen Pydantic contracts for quality, observation, indexed candidate review, additional anomaly, image review and final decision.
- `vision/quality.py`: Pillow-only full-image luminance, contrast and edge-variance metrics. No enhancement, training or external I/O.
- `agent/image_review.py`: `review_required` plus one-shot original-image VLM boundary. Reads at most the configured byte limit plus one byte; accepts decoded JPEG/PNG only; checks source/detection dimensions; does not retry.
- `agent/image_policy.py`: pure deterministic conversion from detection/review evidence to findings, coverage and inspection result.
- Four new focused test modules; shared exports, configuration, storage, workflow, API and existing tests were not edited.

## Review and policy behavior

- Review is required for zero detections, any score below `0.85`, every FS/BX candidate, or inadequate image quality.
- The VLM receives the complete original image as a data URL, indexed detection/quality metadata, `ImageReview.model_json_schema()`, and approved domain definitions: AJ 支管暗接; BLOCK CJ/JG/ZW 合并堵塞; PL 破裂; FS 腐蚀; BX 变形.
- Prompt `image-review-v1` tells the model to treat pixels/metadata as evidence rather than instructions, audit the whole image, review every index once, and provide `proposed_label` for `revise`.
- Missing, duplicate or out-of-range candidate indices and out-of-image additional boxes become safe non-retryable `response_schema_invalid` errors.
- A review agreeing with the original label can confirm adequate, assessable evidence by rules. Weak rejected candidates may be rejected; strong rejection, revised labels, abstention and missing context remain for human review.
- Additional VLM anomalies are always `needs_review`; `OTHER` is never auto-confirmed. A completed assessable zero-detection/full-image audit can conclude normal.
- Review failure, inadequate quality or incomplete required audit never concludes normal. Detection evidence remains represented in the decision.

## Provisional quality gate

- Version: `image-quality-v1`.
- Mean luminance: `35 <= mean <= 220`; contrast standard deviation: `>=18`; cropped FIND_EDGES variance: `>=50`.
- These are engineering admission gates only, not defect probabilities, calibrated accuracy or image enhancement.

## TDD and verification

- Initial RED: focused pytest collection produced four expected missing-module errors.
- Contract GREEN: `6 passed`; quality GREEN: `2 passed`; VLM boundary GREEN: `7 passed`; decision GREEN: `17 passed`.
- Prompt review RED: wire test failed on absent `label_definitions`; GREEN after schema and semantics were included.
- Combined: `32 passed in 0.28s`.
- Ruff on only owned implementation/tests: `All checks passed!`.
- No real VLM/API, paid service, GPU, workflow or database call was made. Root separately reported its Activity tests (`5 passed`) and real Temporal recovery test (`1 passed`).

## Remaining calibration question

- High-confidence AJ/PL/BLOCK with adequate quality skip VLM and are confirmed by the deterministic threshold rule. This follows the designed skip path; whether production policy should require human confirmation for any subset is a later acceptance/calibration decision, not established accuracy here.
