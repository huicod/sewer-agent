# Implementation and verification

1. Root: add session-summary contract/repository read/API and targeted tests. Preserve existing endpoints and validate list responses in the frontend.
2. Tool agent (isolated files): multi-operation lineage migration, original-source resolution only for tool actions, unit plus isolated PostgreSQL tests for sequential/concurrent/new/retry requests and old-row migration. No public DDL during development.
3. Report agent (isolated files): source-preserving session report contracts, loaders and HTML/JSON API; allow list_turns(limit=None) for complete report history; tests for running snapshots, boundaries and escaping.
4. Sidebar agent (isolated new component): conversation list/search/new controls with exact session IDs. Root integrates the independent shell, task panel, compact composer and draft isolation; component regressions cover switching during requests and associated task behavior.
5. Root: run pinned-Node typecheck/unit tests and proportional backend pytest/ruff, then synthetic browser journeys (conversation create/switch, compact controls, responsive layout, image tool follow-ups and unified report). Review actual screenshots and complete diff; request scoped independent review.
6. Root: stop managed app, run existing storage initialization explicitly for additive lineage, build frontend, start managed app; verify dependency/workers ready and page/assets/health HTTP 200. Update examples/conventions, archive task and journal. No live inference, remote pushes or destructive schema operations.
