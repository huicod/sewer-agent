import { expect, test } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

test.beforeEach(async ({ page }) => {
  const response = await page.request.post("/api/sessions");
  expect(response.status()).toBe(201);
  const session = await response.json();
  await page.addInitScript((sessionId: string) => {
    localStorage.setItem("pipe-inspector.session-id", sessionId);
  }, session.session_id);
});

// Catches lost retry identity/duplicate submission after the server committed a turn.
// Only the response is lost: admission, workflow execution and storage stay real.
test("lost admission response reuses the upload and request, then reload restores one task", async ({ page }) => {
  const output = process.env.SEWER_BROWSER_OUTPUT!;
  const screenshots = path.join(output, "screenshots");
  await mkdir(screenshots, { recursive: true });
  const payloads: Record<string, unknown>[] = [];
  const admitted: Record<string, unknown>[] = [];
  const errors: string[] = [];
  let uploads = 0;
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/api/assets")) uploads++;
  });
  await page.route(/\/api\/sessions\/[^/]+\/turns$/, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    payloads.push(route.request().postDataJSON());
    const response = await route.fetch();
    expect(response.status()).toBe(202);
    admitted.push(await response.json());
    if (payloads.length === 1) await route.abort("failed");
    else await route.fulfill({ response });
  });
  await page.goto("/");
  await page.getByText("附件与检查选项", { exact: true }).click();
  await page.getByLabel("对话附件（可选）").setInputFiles(path.join(output, "service/fixtures/image.png"));
  await page.getByLabel("对话内容", { exact: true }).fill("回执丢失验收：检查图片并复核");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(page.getByText(/再次发送将使用相同请求编号/)).toBeVisible();
  expect(admitted).toHaveLength(1);
  await page.screenshot({ path: path.join(screenshots, "06-lost-response.png"), fullPage: true });
  await page.getByRole("button", { name: "重新发送", exact: true }).click();
  await page.getByRole("button", { name: "展开任务详情" }).click();
  await expect(page.getByRole("button", { name: "开始人工复核", exact: true })).toBeVisible();
  expect(payloads).toHaveLength(2);
  expect(admitted).toHaveLength(2);
  expect(payloads[1]).toEqual(payloads[0]);
  expect(admitted[1].request_id).toBe(admitted[0].request_id);
  expect(uploads).toBe(1);
  const sessionId = admitted[0].session_id;
  const sessionResponse = await page.request.get(`/api/sessions/${sessionId}`);
  const turnsResponse = await page.request.get(`/api/sessions/${sessionId}/turns`);
  expect(sessionResponse.status()).toBe(200);
  expect(turnsResponse.status()).toBe(200);
  const session = await sessionResponse.json();
  const turns = await turnsResponse.json();
  expect(session.tasks).toHaveLength(1);
  expect(turns).toHaveLength(1);
  expect(turns[0]).toMatchObject({ request_id: payloads[0].request_id, status: "completed" });
  const taskId = session.tasks[0].snapshot.task_id;
  expect(turns[0].linked_task_id).toBe(taskId);
  expect(session.tasks[0].snapshot.runtime_status).toBe("waiting_review");
  await page.reload();
  await page.getByRole("button", { name: "展开任务详情" }).click();
  await expect(page.locator(".task-card")).toHaveCount(1);
  await expect(page.locator(".task-card").locator(`[title="${taskId}"]`)).toBeVisible();
  await expect(page.getByRole("button", { name: "开始人工复核", exact: true })).toBeVisible();
  expect(errors).toEqual([]);
  await page.screenshot({ path: path.join(screenshots, "07-retry-restored.png"), fullPage: true });
  await writeFile(path.join(output, "lost-response.json"), JSON.stringify({
    payloads, admitted, uploads, session, turns, page_errors: errors,
    synthetic_models: true, real_provider_requests: 0, injection: "real POST accepted; response aborted once",
  }, null, 2));
});
