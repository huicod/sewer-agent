# Approved direction

Keep the existing React/Vite app and state/network logic. Add Ant Design and its icons, configured for Chinese and a blue/neutral theme. Use a narrow task sidebar, white central inspection surface and right-hand assistant. On smaller screens reflow to one column. Manual submission is opened from a prominent new-check action in an accessible modal. Task list navigation/search/status filters select the visible inspection without changing the assistant's explicitly selected task or retry identity.

Extract the existing TaskCard into a focused component while preserving the existing on-demand evidence fetches and exact server-owned result/provenance semantics. Give the task detail a compact toolbar, media preview, readable findings, secondary execution metadata and evidence/review sections. Existing detailed evidence panels stay intact and are restyled consistently.

Palette: #f5f6f8 canvas, #ffffff surfaces, #172b4d text, #64748b secondary, #1677ff primary, #e5e7eb separators. Body text 14px, compact headings, 6px radii, no decorative gradients or large shadows. Empty states describe actual next actions; no invented metrics or uptime badges.

Rollback: the pre-change parent is d3936db9. Changes are isolated in the initially empty jj change uwpsyylp. Only the main agent mutates jj/task state. Delegated writers own disjoint frontend files.
