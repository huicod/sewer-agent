# Ant Design workbench implementation plan

Trellis is the controlling workflow. User approval covers implementation; no further design gate is needed.

- [x] Add antd/icons and theme/provider. Main agent owns package files, App.tsx, main.tsx, styles.css and test setup.
- [x] Add a task-navigation component with meaningful search/status/selection tests. Delegate only new WorkspaceSidebar.tsx and its test file.
- [x] Extract and restyle TaskCard, preserving evidence/review semantics. Delegate only new TaskCard.tsx and task-card.css; existing behavior tests are the regression baseline.
- [x] Integrate the sidebar, selected task, assistant and new-check modal. Adjust existing tests only where the user journey changes; add integration coverage for navigation without changing assistant targets.
- [x] Run unit tests and build with project-local Node; run synthetic Playwright journeys and inspect desktop/mobile screenshots.
- [x] Obtain one independent code review, resolve material findings, inspect complete jj diff, update conventions and record evidence.
- [x] Stop/build/start sequentially via scripts/app.py; verify service readiness and HTTP assets; deliver access URL.

Commands: `PATH="$PWD/.tools/node/bin:$PATH" npm --prefix frontend test`, `PATH="$PWD/.tools/node/bin:$PATH" npm --prefix frontend run build`, `PATH="$PWD/.tools/node/bin:$PATH" npm --prefix frontend run test:e2e`.
