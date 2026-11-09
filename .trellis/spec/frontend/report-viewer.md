# Inspection report viewer

## Scope / trigger
Apply when changing the task/session report viewer or authoritative HTML renderer. This is a read-only presentation of stored reports; no model execution, review decision or schema migration is implied.

## Signatures
- `ReportViewer({ source: { kind: "session" | "task", id }, initialFormat: "html" | "json", open, onClose })`.
- `getInspectionReport(source)` and `getInspectionReportHtml(source)` own network access in `api.ts`.
- Existing `GET /api/sessions/{id}/report?format=json|html` and task equivalents remain authoritative. Task reports require a terminal task; session reports permit non-final snapshots.

## Contracts
- Task reports preserve `task`, `tool_calls` and optional saved evidence; session reports preserve `session`, `generated_at`, `is_final`, `turns`, `asset_groups` and exact source roles/bindings.
- JSON selection presents actual values; absent heterogeneous fields display `未提供`, explicit null remains `null`, and zero remains zero. Copy/download/raw views preserve the complete document.
- HTML is fetched and rendered through `srcDoc` in a sandbox without `allow-scripts`. Inject restrictive metadata CSP because fetch response headers do not apply to srcDoc. Permit same-origin evidence images and inline styles; preserve fragment navigation and open file/API links separately. Do not relax the server's `frame-ancestors 'none'` policy.
- HTML renderers escape user/model text, use real evidence references and known image/frame provenance, and keep unknown file types as links. Grouping does not deduplicate physical defects across sources.

## Validation and error matrix
| Condition | Required behavior |
| --- | --- |
| Wrong session/task or inconsistent source AssetRef | Reject at API validation boundary |
| One report format fails | Show its error; retain independently successful format |
| Source changes or viewer closes during a request | Ignore the late response |
| Clipboard, download or frame printing fails | Show actionable feedback and keep original open/download access |
| Active task/session | Do not label a non-final snapshot as final |

## Good / base / bad cases
- Good: use the stored source review status, original score and evidence URL.
- Base: a task with no findings reports its saved result and coverage; absence alone is not normality.
- Bad: sum inherited findings, invent project metadata or use an unframed raw JSON text wall as the sole report view.

## Tests required
- `ReportViewer.test.tsx`: source switching, loading/failure, text escaping, tree/table/raw, copy failure, unknown fields and missing/null distinction.
- `test_formal_report_document.py` plus existing report regressions: escaped text, source scope, evidence previews and non-final state.
- Real browser: CSP-safe actual images, fragment navigation, print path, narrow tables, long IDs, and unchanged underlying draft. Unit DOM tests do not establish iframe media or visual correctness.

## Wrong versus correct
- Wrong: point an iframe directly at the report endpoint blocked by `frame-ancestors 'none'`.
- Correct: fetch escaped HTML, add the explicit inert-document policy, and verify real asset loading in the sandboxed srcDoc.
