# T02 structured API adapter evidence

Historical adapter implementation evidence. Actual Qwen text/image integration subsequently passed: [Qwen validation](../09-09-m1-image-workflow/qwen-live-success.md).

## Scope

- Added `pipe_inspector.agent.OpenAICompatibleClient` and safe `APIClientError` metadata.
- Added local `httpx.MockTransport` contract tests only; no live provider request was made.
- Did not modify Settings, packaging, workflow code, Git, or jj state.

## Implemented contract

- Constructor accepts an HTTP(S) base URL, nonempty model, `SecretStr` key, timeout, and
  optional test transport. Embedded URL credentials and invalid/empty values are rejected.
- `generate()` makes one non-streaming `POST` to `{base_url}/chat/completions`, requests
  JSON-object output, and validates message content with the caller's Pydantic model.
- Redirects are not followed. The client contains no retry loop; Temporal remains the retry owner.
- Timeout, network, HTTP 429, and HTTP 5xx failures are marked retryable.
- Configuration, redirect, authentication, other HTTP 4xx, refusal, empty/truncated response,
  malformed JSON, and response-schema failures are marked non-retryable.
- Exposed errors contain only a stable code, retryable flag, and optional HTTP status. They do not
  include the API key, request messages, or remote response body.

## TDD evidence

- RED 1: `.venv/bin/python -m pytest backend/tests/test_api_client.py -q` failed collection with
  `ModuleNotFoundError: No module named 'pipe_inspector.agent'` before production files existed.
- GREEN 1: the focused file passed `21 passed in 0.16s` after the minimal adapter was added.
- RED 2: a malformed provider envelope whose `message` was a list produced an unhandled
  `AttributeError`; this proved the new schema-boundary test exercised the real parser.
- GREEN 2: after categorizing that envelope safely, the focused file passed `22 passed in 0.16s`.
- Focused lint: `.venv/bin/ruff check backend/src/pipe_inspector/agent
  backend/tests/test_api_client.py` returned `All checks passed!`.

## Concurrent-suite status

- A full `.venv/bin/python -m pytest backend/tests -q` run during parallel detector development
  reported `28 passed, 13 failed`; all 13 failures were in `backend/tests/test_detector.py` against
  the then-incomplete `vision/detector.py`, outside T02 ownership.
- A concurrent full ruff run likewise found one import-order issue only in
  `backend/tests/test_detector.py`; T02-owned paths are clean.

## Unresolved integration

- Live OpenAI-compatible provider behavior remains unverified because no provider endpoint/key was
  configured and this task forbids live calls. MockTransport results do not claim provider readiness.
