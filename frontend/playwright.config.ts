import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const live = process.env.SEWER_RUN_BROWSER_LIVE === "1";
if (live && (process.env.SEWER_RUN_LIVE_API !== "1" || !path.isAbsolute(process.env.SEWER_LIVE_IMAGE ?? ""))) {
  throw new Error("Live browser acceptance requires SEWER_RUN_LIVE_API=1 and an absolute SEWER_LIVE_IMAGE");
}
const output = process.env.SEWER_BROWSER_OUTPUT ?? path.join(
  root, "var/evaluations/browser", `run-${Date.now()}-${process.pid}`,
);
process.env.SEWER_BROWSER_OUTPUT = output;
process.env.PLAYWRIGHT_BROWSERS_PATH ??= path.join(root, ".tools/playwright");
process.env.FONTCONFIG_FILE ??= path.join(root, "frontend/e2e/fonts.conf");
process.env.XDG_DATA_HOME = path.join(root, ".tools");
process.env.XDG_CACHE_HOME = path.join(root, ".tools/cache");

export default defineConfig({
  testDir: "./e2e",
  testMatch: live ? "**/live.spec.ts" : ["**/core.spec.ts", "**/faults.spec.ts"],
  workers: 1,
  retries: 0,
  timeout: live ? 600_000 : 120_000,
  expect: { timeout: 20_000 },
  outputDir: path.join(output, "test-results"),
  reporter: [["list"], ["json", { outputFile: path.join(output, "browser-results.json") }]],
  use: { browserName: "chromium", baseURL: "http://127.0.0.1:18081",
    viewport: { width: 1440, height: 1000 }, trace: "retain-on-failure" },
  webServer: [
    {
      command: ".venv/bin/python backend/tests/browser_server.py",
      cwd: root,
      url: "http://127.0.0.1:18080/health",
      env: {
        SEWER_RUN_BROWSER_TEST: "1", SEWER_BROWSER_OUTPUT: path.join(output, "service"),
        SEWER_RUN_BROWSER_LIVE: live ? "1" : "0",
        SEWER_RUN_LIVE_API: process.env.SEWER_RUN_LIVE_API ?? "0",
        SEWER_LIVE_IMAGE: process.env.SEWER_LIVE_IMAGE ?? "",
      },
      reuseExistingServer: false,
      gracefulShutdown: { signal: "SIGTERM", timeout: 30_000 },
      timeout: 30_000,
    },
    {
      command: "node_modules/.bin/vite --config vite.e2e.config.ts --port 18081 --strictPort",
      url: "http://127.0.0.1:18081",
      reuseExistingServer: false,
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
      timeout: 30_000,
    },
  ],
});
