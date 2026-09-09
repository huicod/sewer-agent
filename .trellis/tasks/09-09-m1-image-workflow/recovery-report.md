# T05 recovery review fixes

- Important 1: detect retries at most 3 times for transport/worker failure; existing deterministic model/config/OOM failures remain nonretryable. Successful checkpoints bypass another GPU invocation.
- Important 2: final idempotent DB persistence retries durably with exponential backoff capped at 30 seconds. It only reuses stored observations/reviews and cannot call VLM/detector again. Policy/Prompt drift ends explicitly failed/inconclusive, retaining raw candidates as needs_review without executing the changed policy.
- Finalization exposes reporting / 95%, records specific review error codes, and marks interrupted running attempts uncertain. Final checkpoint and task projection are recoverably convergent writes, not one atomic transaction.
- Focused Activity/fault tests: 11 passed in 0.62s. Tests cover lost detection reply, cached final projection repair with stable IDs, version drift, specific errors, and interrupted tool attempts.
- Added real local Temporal/PG fault test with a lost response after committed detection and a once-failing terminal projection; **1 passed in 3.98s**; detect/finalize attempts=[1,2], model=1, VLM substitute=1, final payload/finding IDs stable. Final independent re-review PASS. No real VLM or additional GPU inference is used.
- Root completed the final fault tests after the implementation worker was delayed by the known patch-read sandbox issue. No extra dependencies, credentials, services, or scheduler were introduced.
