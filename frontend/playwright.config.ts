import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
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
  workers: 1,
  retries: 0,
  timeout: 120_000,
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
      env: { SEWER_RUN_BROWSER_TEST: "1", SEWER_BROWSER_OUTPUT: path.join(output, "service") },
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
