# T02 API adapter subtask

Create `backend/src/pipe_inspector/agent/api_client.py` (and package init) and `backend/tests/test_api_client.py` only. No Settings, workflow or packaging edits.

Implement an explicitly named OpenAICompatibleClient using httpx.AsyncClient. Constructor: base_url, model, api_key (SecretStr), timeout_seconds=60, optional httpx transport for tests. `async generate(messages, response_model)` posts to `{base_url}/chat/completions` with model/messages and JSON-object response format, then validates message content against the supplied Pydantic model. No streaming, provider guessing or automatic fallback models.

The adapter does not retry: Temporal owns bounded retries. Categorize timeouts/network/429/5xx as retryable errors and invalid configuration/auth/4xx/schema/refusal/empty/truncated response as non-retryable errors. Exceptions must expose safe code, retryable flag, optional status; never expose key, request messages or remote body. Accept only HTTP(S) server-configured base URLs and nonempty model/key. Do not follow redirects with bearer credentials.

Tests first, use httpx.MockTransport only at the external boundary; verify actual request method/path/JSON/auth plus successful schema parsing, timeout, rate limit, auth failure, malformed or wrong-schema JSON, empty/refused/truncated content, and safe errors. No live API requests without configured provider/key. No new dependencies or nested agents. Report RED/GREEN evidence and unresolved live integration status in api-report.md.
