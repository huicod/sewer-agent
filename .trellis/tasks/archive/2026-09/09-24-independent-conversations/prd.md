# Independent inspection conversations

User requested an Ant Design independent conversation layout, more usable message space, image-scoped multi-turn task context, repeatable tool calls and a unified report.

Acceptance:
- Left navigation lists persistent sessions/conversations rather than every execution. New/switch/search conversations; existing session/history remains accessible. Default workflow is a conversation for an image; multiple assets can be explicitly added to an existing conversation.
- Conversation is the main surface. Compact composer with attachment/mode/target options collapsed, compact context summary, scrollable transcript. Task/evidence panel toggles, resizes, and groups same-asset executions; side navigation can collapse. Mobile does not overflow.
- Every user-requested execution remains a separate immutable task inside its conversation. Selecting a child and asking for another image tool resolves the original source, preserves session/asset/mode checks and request retry identity. Multiple image tools are append-only and do not overwrite original evidence.
- Unified session JSON/HTML report groups stored sources by exact AssetRef and retains task/parent IDs, status, coverage, evidence and tool outcomes. Running snapshots are explicitly non-final; no invented cross-run unique defect count or replacement of human decisions.
- Drafts/explicit source selections are isolated across conversation switches; historical task/turn data is restored. Background requests never populate a different conversation.
- Existing original intake, video/frame tools, comparison, review and report paths remain operable. No live model calls or false human decisions during implementation.

Trellis owns this work. jj change yqvryysp; parent 31b7b332 preserves completed prior iteration. Reference: https://x.ant.design/ (Web Independent) and official Conversations docs. Use installed Ant Design primitives; no new chat SDK or model/provider path.
