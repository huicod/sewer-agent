# Engineering workbench and structured reports

## Approved direction
The user explicitly approved implementation on 2026-11-09: option 02 steel-blue engineering workbench, option 05 formal HTML report, and option 04 structured JSON viewer. This is the local pipe inspection application, not the personal website. Reference images are visual concepts only; their example records must never enter runtime data.

## Requirements
- Make original image/video the primary work surface, with direct file access and persisted evidence/timeline nearby. Keep continuing conversation and actual findings convenient in a compact supporting pane.
- Apply a restrained contemporary engineering style, compact steel-blue navigation, clear panel headings, small corner radii, readable tables and deliberate spacing.
- Render HTML reports as formal documents with actual metadata, snapshot caveats, findings, source/evidence links and image previews. Provide print and download/open actions.
- Render JSON reports as navigable structured data with selection details/tables; retain complete raw JSON, copying and downloading.
- Support both unified session reports and individual task report entry points, preserving report scope labels and active/non-final status.
- Preserve all records, input assets, saved results, review provenance, settings, session drafts, file references, frame selection and retry identities.
- No new database schema, model executions, fabricated assistant replies, simulated inspection results, inferred physical measurements or GIS features.

## Acceptance
- Existing frontend behavioral suite and build pass; targeted backend report tests verify escaping, data preservation and actual evidence rendering.
- Browser acceptance at desktop and mobile: substantial main image/video, usable continuing dialogue, source links, thumbnail previews, structured JSON and formal HTML report, narrow-layout navigation and draft continuity.
- Verify actual local persisted record counts/source identities before and after deployment; inspect real images/video without submitting inspections or human decisions.
- Record tested limitations and deliver implementation on the existing latest deployment branch with no unrelated changes.
