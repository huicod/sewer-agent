# Structure and contracts

Session ID is conversation identity. GET /api/sessions lists server-derived summaries (session_id, created_at, updated_at, title, task_count, turn_count); title comes from first stored user input/task instruction, not another model call. The current session endpoint continues to return exact task records. Existing sessions remain intact instead of silently splitting/deleting data.

The independent shell owns conversation navigation. Its keyed conversation controller owns server tasks/turns and per-conversation composer state; outgoing request results cannot land in another conversation. Session switches cache local drafts and retry objects. The central transcript uses available height; auxiliary options and source metadata are collapsed. The evidence panel shows a compact same-asset task history and the existing TaskCard, is hidden on demand, and keeps resize/keyboard controls. Reports are linked from the conversation header.

Image tool dispatch resolves a selected supplement to its original image for a fresh explicit operation. The original run remains frozen and each operation gets its own task and checkpoints. A new lineage table is initialized additively and backfilled from the existing one-slot table without deleting it. New operations use the new table; request ID controls retry identity. Each run's existing operation budget stays bounded.

Session report sources preserve their exact task IDs and stored parent links. Grouping exact asset identity improves navigation without claiming physical finding matches. All snapshots and tool results remain attributable to each execution; completed/partial/failed/running states are preserved.
