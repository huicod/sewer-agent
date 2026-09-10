# Qwen actual integration — 2026-09-10

- Installed optional SDKs in the project .venv: openai 3.11.0, dashscope 1.27.4; pinned as pyproject.toml extra qwen-sdk. Existing production httpx client remains unchanged.
- Verified Settings reads the .env DASHSCOPE_API_KEY correctly (boolean comparison only; no secret output).
- User-supplied Responses legacy endpoint: completed, answer 9.9 (256 output-token cap, thinking disabled, SDK retries disabled).
- User-supplied DashScope native endpoint: HTTP 200, correctly described the public dog-and-girl reference image (256 output-token cap).
- Existing production Chat Completions structured text test: 1 passed in 2.03s, max output 256. No production client code change was needed.
- Existing ImageReviewer on var/fixtures/sewer2403-sample.jpg plus saved real detector batch: 1 passed in 10.43s. Full-image structured response accepted AJ candidate index 0 and proposed an additional FS candidate. Additional findings are suggestions requiring human review; this is not accuracy validation.
- Previous authentication_failed is no longer reproducible. SDK installation fixes missing imports, but evidence does not establish the cause of the historical authentication failure.
- Official Responses documentation now deprecates the /api/v2/apps/protocols/compatible-mode/v1/responses path; successful reproduction does not make it the chosen production path. https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses
- No key was printed or committed; CCTV upload was one selected original image. No automatic cross-region/provider fallback or model training.
