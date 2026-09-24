# Assistant usability after first manual inspection

User requested fixes after running example01: right assistant cannot widen, binding/context is cumbersome, and geometry follow-up on detector-only task is refused without usable recovery.

- Add persistent drag/keyboard adjustable assistant width with responsive clamping.
- Default assistant context follows displayed task. Explicit target choice and in-progress drafts pin target; render a visible context card and a one-click return to displayed task. Preserve binding after sends, bind newly admitted upload/manual/recovery tasks and keep retry request identity immutable.
- Show detector-only capability limits before submission. Offer explicit use-same-original createTask(yolo_vlm) then startTask recovery; preserve existing mode/results and geometry draft. Explain and guard baseline waiting until completed/partial before the draft is sent. Never automatically run real user inputs during implementation.
- Improve target controls/transcript labels; maintain frame/reference selection semantics. Keep the existing on-demand comparison panel: comparisons can be inferred by the planner without an explicit reference_task_id, so that field cannot reliably hide the control.
- Update examples/conventions to match new interaction. No backend contract or inference-policy rewrite required.

Trellis controls task state in current follow-up jj change kvlkotxo. Delegated file scopes: useAssistantResize.ts/test; GuidedRestartAction.tsx/test. Root owns App/styles/integration/e2e/docs. Meaningful unit regressions plus synthetic browser acceptance, independent review, then sequential stop/build/start and HTTP smoke. No live model calls or fabricated human reviews.
