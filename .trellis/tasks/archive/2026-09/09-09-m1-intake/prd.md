# T04 — Media intake, persistent tasks and minimal Web UI

Source: approved DEVELOPMENT_PLAN.md T04. Predecessor T03 completed at jj `5d42d0d0`; T02 only lacks live API configuration. This task is in progress; binding interfaces and acceptance are in design.md.

Goal: upload CCTV images/offline videos, retain original assets, submit text instructions associated with a session, and query persistent task progress through a minimal usable page. T06 supplies multi-turn answers and linked follow-up tool actions; T04 does not pretend to execute them.

Requirements:
- Validate actual media content, not just filename or client MIME; reject unsupported/corrupt/oversized inputs with clear errors. Video metadata uses real media probing.
- Store original bytes below the configured asset root, referenced by UUID/version; clients cannot choose server filesystem paths. Do not hash uploads or overwrite original evidence.
- PostgreSQL stores assets, text instructions and task projections; reload/restart must preserve accepted tasks. APIs consume/export the existing T03 contracts.
- Provide upload, task creation and progress lookup endpoints, plus a minimal React/TypeScript Web page for upload/text/status/errors. Keep storage/network configuration server-side, with no credentials in frontend output.
- T04 creates durable queued tasks; actual detector/VLM execution and InspectionWorkflow integration belong to T05. The page must not imply detection is running or completed when only intake is implemented.
- Tests cover valid image/video metadata, malformed input, path rejection, persistence across repository reconnect, not-found tasks and UI loading/error/progress states. Use synthetic media for mechanics; real CCTV video effect acceptance remains a later requirement.

Implementation follows implement.md with disjoint storage/frontend/root scopes. No extra orchestrator, model deployment or production exposure is required.
