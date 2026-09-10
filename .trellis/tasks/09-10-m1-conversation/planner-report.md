# T06-A planner report

## Status

- Added constrained conversation input/intent contracts.
- Added one-shot structured intent parsing through the existing API client. The actual HTTP request now includes the complete `ConversationIntent` JSON schema in its system message.
- Added deterministic compilation into the existing `TaskPlan`; non-inspection intents have no plan.
- Scope remains parsing and plan foundations only. No HTTP route, database, Temporal, UI, environment, or live API integration was added.

## TDD evidence

- Initial RED: `.venv/bin/python -m pytest backend/tests/test_conversation_intent.py -q`
  - Expected collection failure: `ModuleNotFoundError: pipe_inspector.agent.intent`.
- Initial GREEN: `.venv/bin/python -m pytest backend/tests/test_conversation_intent.py -q`
  - `35 passed in 0.20s`.
- Review-fix RED: focused MockTransport test captured the outgoing HTTP payload and failed because its system message did not contain `ConversationIntent.model_json_schema()` (`1 failed, 35 passed`).
- Review-fix GREEN: `.venv/bin/python -m pytest backend/tests/test_conversation_intent.py -q`
  - `36 passed in 0.20s`.
- Focused lint: `.venv/bin/ruff check backend/src/pipe_inspector/contracts/conversation.py backend/src/pipe_inspector/agent/intent.py backend/tests/test_conversation_intent.py`
  - `All checks passed!`.
- Earlier full-suite checkpoint, before the schema-payload follow-up: `.venv/bin/python -m pytest backend/tests -q`
  - `284 passed, 23 skipped in 4.38s`. Per integration-owner direction, the full suite was not rerun after the focused follow-up; the root Agent owns final integration verification.

## Key unknowns / integration boundary

- The caller still needs to supply the authoritative runtime `available_actions` set.
- The workflow's quality/candidate policy still owns whether the mandatory `verify` plan node invokes external review; this slice only prevents the node from being omitted during planning.
- Product conversation routing, persistence, and execution remain outside T06-A.
