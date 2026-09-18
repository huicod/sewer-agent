# Conservative VLM fusion policy

## Goal

Make YOLO-guided VLM review conservative by default: preserve the detector
candidate set as the recall floor, expose VLM disagreements and additions as
auditable advice, and prevent uncalibrated VLM output from silently becoming a
confirmed deletion, relabel, or new defect.

## Requirements

- The guided reviewer remains active for every configured image review; this
  task does not add another detector, provider, or API call.
- The default fusion policy must keep every original YOLO box when VLM advice
  is reject, revise, uncertain, missing, invalid, or unavailable.
- A clear VLM accept may confirm the original candidate, but must not change
  its class, box, or detector score.
- A VLM revise/reject must remain in the structured change log with its reason
  and proposed label, and must be visibly pending human review.
- VLM additional anomalies must be retained as suggestions and pending human
  review by default; they must not enter default scored predictions.
- The policy version must be distinct from the historical
  `guided-confidence-v2` policy so saved runs cannot be silently reinterpreted.
- Historical fusion can still be replayed explicitly for comparison; normal
  online and offline paths use the conservative policy.
- No credentials, model weights, datasets, or generated galleries are changed
  by this task.

## Acceptance Criteria

- [x] New unit tests fail before the production policy change and pass after it.
- [x] Default fusion keeps original boxes for low-confidence reject/revise and
  for all pending additions.
- [x] Online guided decisions mark those disagreements/additions as
  `needs_review`, not `confirmed` or silently `rejected`.
- [x] An explicit legacy policy reproduces the former delete/revise/add rules.
- [x] Saved policy pins distinguish the new policy from `guided-confidence-v2`.
- [x] Focused backend tests and the full relevant test suite pass.
- [x] An offline replay over the cached 606-image review responses produces a
  new, credential-free summary without another VLM API call.
