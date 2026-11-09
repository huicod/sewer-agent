# Design

Preserve the existing App session/draft cache and InspectionConversation controller. Reflow presentation into a primary media/task surface and supporting conversation pane. Reuse TaskInputPreview, VideoTimeline, EvidenceThumbnail and existing TaskCard controls instead of introducing a second inspection state machine. Keep existing backend review and evidence semantics.

Report data continues to originate at existing task/session report endpoints. Improve server HTML document rendering without changing stored data or snapshot aggregation. Add an on-demand report viewer with HTML and structured JSON tabs. Keep report loading errors visible, avoid stale responses when scope changes, render text safely, and preserve raw data/export access. HTML endpoint currently forbids framing; use a sandboxed srcDoc from fetched HTML or explicit safe supported rendering rather than a blocked direct iframe URL. No executable report content.

Ownership: main agent owns layout, integration, styles, task/journal, verification and jj operations. Atomic report frontend/backend workers own only agreed report files and relevant tests. Other changes in the original root checkout are unrelated and untouched.
