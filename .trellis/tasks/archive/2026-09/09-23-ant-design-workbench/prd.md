# Ant Design inspection workbench

User approved Ant Design and a business-workbench direction on 2026-09-23 and explicitly requested implementation.

## Requirements
- Replace the dark gradient/card-heavy presentation with a restrained, light, Chinese-language workbench.
- Provide task navigation, search/status filters, a central inspection area and a side assistant.
- Preserve uploads, saved inspection modes, retries, polling, evidence, human review, optional tools and reports.
- Keep FastAPI/Temporal contracts and stored data unchanged; use same-origin API access.
- Support desktop and narrow screens with keyboard-accessible controls.
- Rebuild and restart the existing localhost:8000 service for user inspection.

## Acceptance
- Frontend unit tests, TypeScript build and synthetic browser journeys pass.
- Task filtering/navigation and new-check entry are tested.
- Browser screenshots show no horizontal overflow at desktop/mobile sizes, no page errors, and working actual controls.
- No real-model calls or fabricated reviews of real evidence are made during verification.
