// Explicit local deployment acceptance. No inspection, VLM request or human decision.
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const root = fileURLToPath(new URL("../../../", import.meta.url));
const output = process.argv[2];
assert.equal(process.env.SEWER_RUN_DELIVERY_TEST, "1", "explicit deployment-test gate required");
assert(output && path.isAbsolute(output) && output.startsWith(path.join(root, "var/evaluations/")));
await mkdir(output); // Preserve old evidence: refuse an existing output directory.
process.env.PLAYWRIGHT_BROWSERS_PATH = path.join(root, ".tools/playwright");
process.env.FONTCONFIG_FILE = path.join(root, "frontend/e2e/fonts.conf");
process.env.XDG_DATA_HOME = path.join(root, ".tools");
process.env.XDG_CACHE_HOME = path.join(root, ".tools/cache");
const { chromium, expect } = await import("../../../frontend/node_modules/@playwright/test/index.mjs");
const execute = promisify(execFile);
const records = [];
async function control(command) {
  const started = performance.now();
  const result = await execute(path.join(root, ".venv/bin/python"), ["scripts/app.py", command], {
    cwd: root, timeout: 120_000,
  });
  const state = JSON.parse(result.stdout);
  records.push({ command, elapsed_seconds: (performance.now() - started) / 1000, state });
  await writeFile(path.join(output, "lifecycle.json"), JSON.stringify(records, null, 2));
  return state;
}
const pids = (state) => Object.fromEntries(Object.entries(state.components).map(([k, v]) => [k, v.pid]));
function ready(state) {
  assert.deepEqual(Object.keys(state.components).sort(), ["api", "cpu", "gpu"]);
  assert(Object.values(state.components).every((c) => c.running && c.ready));
  assert(state.ready && state.dependencies.postgres.running && state.dependencies.postgres.ready
    && state.dependencies.temporal.ready);
}
assert(Object.values((await control("status")).components).every((c) => !c.running),
  "do not stop an already running application for this test");
let browser;
let success = false;
try {
  const first = await control("start");
  ready(first);
  const duplicate = await control("start");
  ready(duplicate);
  assert.deepEqual(pids(first), pids(duplicate));
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  const turnWrites = [];
  const staticResponses = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/turns")) turnWrites.push(request.url());
  });
  page.on("response", (response) => {
    if (/\/assets\/.*\.(js|css)$/.test(response.url())) {
      staticResponses.push({ url: response.url(), status: response.status() });
    }
  });
  const created = page.waitForResponse((r) => r.url().endsWith("/api/sessions") && r.request().method() === "POST");
  await page.goto("http://127.0.0.1:8000");
  const admitted = await created;
  assert.equal(admitted.status(), 201);
  const session = await admitted.json();
  await expect(page.getByRole("heading", { name: "管道影像检查" })).toBeVisible();
  await expect(page.getByLabel("对话内容", { exact: true })).toBeVisible();
  await page.screenshot({ path: path.join(output, "01-running.png"), fullPage: true });
  await control("stop");
  const stopped = await control("status");
  assert(Object.values(stopped.components).every((c) => !c.running));
  assert(stopped.dependencies.postgres.running && stopped.dependencies.postgres.ready
    && stopped.dependencies.temporal.ready);
  ready(await control("start"));
  const restarted = await control("restart");
  ready(restarted);
  for (const role of ["api", "cpu", "gpu"]) assert.notEqual(pids(first)[role], pids(restarted)[role]);
  await page.reload();
  await expect(page.getByText(session.session_id, { exact: true })).toBeVisible();
  const savedResponse = await page.request.get(`http://127.0.0.1:8000/api/sessions/${session.session_id}`);
  assert.equal(savedResponse.status(), 200);
  const saved = await savedResponse.json();
  assert.equal(saved.session.session_id, session.session_id);
  assert.equal(Date.parse(saved.session.created_at), Date.parse(session.created_at));
  assert.deepEqual(saved.tasks, []);
  const turnsResponse = await page.request.get(`http://127.0.0.1:8000/api/sessions/${session.session_id}/turns`);
  assert.equal(turnsResponse.status(), 200);
  const turns = await turnsResponse.json();
  assert.deepEqual(turns, []);
  assert.deepEqual(errors, []);
  assert.deepEqual(turnWrites, []);
  assert(staticResponses.length >= 2 && staticResponses.every((r) => r.status === 200));
  await page.screenshot({ path: path.join(output, "02-restarted.png"), fullPage: true });
  await writeFile(path.join(output, "browser.json"), JSON.stringify({
    session, saved, turns, page_errors: errors, turn_writes: turnWrites, static_responses: staticResponses,
    model_requests: 0, note: "Empty deployment acceptance session; no inspection was submitted.",
  }, null, 2));
  success = true;
  console.log(JSON.stringify({ output, session_id: session.session_id, application_left_running: true }));
} finally {
  await browser?.close();
  if (!success) await control("stop");
}
