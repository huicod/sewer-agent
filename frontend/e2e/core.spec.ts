import { expect, test, type Locator, type Page } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

// Synthetic media + model adapters; HTTP, browser, storage and workflows are real.
const output = process.env.SEWER_BROWSER_OUTPUT!;

test.beforeEach(async ({ page }) => {
  const response = await page.request.post("/api/sessions");
  expect(response.status()).toBe(201);
  const session = await response.json();
  await page.addInitScript((sessionId: string) => {
    localStorage.setItem("pipe-inspector.session-id", sessionId);
  }, session.session_id);
});

async function openOptions(page: Page): Promise<void> {
  const options = page.locator("details.composer-options");
  if (!(await options.evaluate((node: HTMLDetailsElement) => node.open))) {
    await options.getByText("附件与检查选项", { exact: false }).click();
  }
}

async function openTaskDetails(page: Page): Promise<void> {
  if (!(await page.getByLabel("任务与证据").isVisible())) {
    await page.getByRole("button", { name: "展开任务详情" }).click();
  }
}

test("conversation layout, draft isolation and narrow-screen navigation", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await mkdir(path.join(output, "screenshots"), { recursive: true });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "从一份现场影像开始" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "检查对话", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "检查助手", exact: true })).toHaveCount(0);
  await expect(page.locator("details.composer-options")).not.toHaveAttribute("open", "");
  await expect(page.getByLabel("任务与证据")).not.toBeVisible();
  const originalWidth = await page.locator(".conversation-main-surface").evaluate((node) => node.clientWidth);
  expect(originalWidth).toBeGreaterThan(800);
  await openTaskDetails(page);
  const separator = page.getByRole("separator", { name: "调整任务详情宽度" });
  const handle = (await separator.boundingBox())!;
  const originalDetailWidth = await page.getByLabel("任务与证据").evaluate((node) => node.clientWidth);
  await page.mouse.move(handle.x + handle.width / 2, handle.y + 80);
  await page.mouse.down();
  await page.mouse.move(handle.x - 140, handle.y + 80);
  await page.mouse.up();
  await expect.poll(() => page.getByLabel("任务与证据").evaluate((node) => node.clientWidth))
    .toBeGreaterThan(originalDetailWidth + 130);
  const resizedWidth = await separator.getAttribute("aria-valuenow");
  await page.reload();
  await openTaskDetails(page);
  await expect(separator).toHaveAttribute("aria-valuenow", resizedWidth!);
  await page.screenshot({ path: path.join(output, "screenshots/workbench-desktop.png"), fullPage: true });
  await page.getByLabel("对话内容", { exact: true }).fill("第一个对话的草稿");
  const firstSession = await page.evaluate(() => localStorage.getItem("pipe-inspector.session-id"));
  await page.getByRole("button", { name: "新建对话" }).click();
  await expect(page.getByLabel("对话内容", { exact: true })).toHaveValue("");
  const secondSession = await page.evaluate(() => localStorage.getItem("pipe-inspector.session-id"));
  expect(secondSession).not.toBe(firstSession);
  await page.getByLabel("对话内容", { exact: true }).fill("第二个对话的草稿");
  await page.getByRole("button", { name: /打开对话：新对话/ }).last().click();
  await expect(page.getByLabel("对话内容", { exact: true })).toHaveValue("第一个对话的草稿");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(separator).not.toBeVisible();
  await expect(page.getByRole("button", { name: "打开对话导航" })).toBeVisible();
  await expect(page.getByRole("button", { name: "新建对话" })).not.toBeVisible();
  await page.getByRole("button", { name: "打开对话导航" }).click();
  await expect(page.getByRole("button", { name: "新建对话" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "打开对话导航" })).toBeFocused();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: path.join(output, "screenshots/workbench-mobile.png"), fullPage: true });
  expect(errors).toEqual([]);
});

async function inspect(page: Page, file: string, instruction: string): Promise<string> {
  await openOptions(page);
  await page.getByLabel("对话附件（可选）").setInputFiles(path.join(output, "service/fixtures", file));
  await page.getByLabel("对话内容", { exact: true }).fill(instruction);
  const submitted = page.waitForResponse((response) =>
    /\/api\/sessions\/[^/]+\/turns$/.test(response.url()) && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  const admitted = await submitted;
  expect(admitted.status()).toBe(202);
  const turn = await admitted.json();
  let taskId = "";
  await expect.poll(async () => {
    const response = await page.request.get(`/api/sessions/${turn.session_id}/turns/${turn.request_id}`);
    const saved = await response.json();
    expect(saved.status).not.toBe("failed");
    taskId = saved.linked_task_id ?? "";
    return saved.status;
  }).toBe("completed");
  expect(taskId).toMatch(/^[0-9a-f-]{36}$/);
  return taskId;
}

function card(page: Page, taskId: string): Locator {
  return page.locator(".task-card").filter({ has: page.locator(`[title="${taskId}"]`) });
}

async function review(page: Page, taskId: string): Promise<void> {
  await openTaskDetails(page);
  const task = card(page, taskId);
  await expect(task.getByRole("button", { name: "开始人工复核" })).toBeVisible();
  const stored = await (await page.request.get(`/api/tasks/${taskId}`)).json();
  expect(stored.snapshot.runtime_status).toBe("waiting_review");
  await task.getByRole("button", { name: "开始人工复核" }).click();
  await expect(task.getByRole("heading", { name: "人工复核", exact: true })).toBeVisible();
  await task.getByLabel("审核人（自报身份）").fill("browser-fixture-reviewer");
  const findings = task.locator(".review-finding");
  await expect(findings).toHaveCount(1);
  await findings.getByRole("radio", { name: "确认", exact: true }).check();
  await findings.getByLabel("处理理由").fill("受控合成夹具的软件验收，不代表真实缺陷人工判定");
  await task.getByRole("button", { name: "提交完整复核" }).click();
  await expect(task.getByRole("link", { name: "JSON 报告", exact: true })).toBeVisible();
  const report = await (await page.request.get(`/api/tasks/${taskId}/report?format=json`)).json();
  expect(report.task.snapshot.runtime_status).toBe("completed");
  expect(report.task.snapshot.findings).toHaveLength(1);
  expect(report.task.snapshot.findings[0]).toMatchObject({
    canonical_label: "AJ", review_status: "confirmed", confirmed_by: "human",
  });
  await writeFile(path.join(output, `${taskId}-report.json`), JSON.stringify(report, null, 2));
}

test("pure YOLO geometry draft is preserved through guided VLM recovery", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await mkdir(path.join(output, "screenshots"), { recursive: true });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "检查对话", exact: true })).toBeVisible();
  await openOptions(page);

  await page.getByLabel("对话附件（可选）").setInputFiles(
    path.join(output, "service/fixtures/image.png"),
  );
  await page.getByLabel("检查模式", { exact: true }).selectOption("yolo_only");
  await page.getByLabel("对话内容", { exact: true }).fill(
    "浏览器验收纯 YOLO 图片：请检查五类缺陷",
  );
  const sourceAdmission = page.waitForResponse((response) =>
    /\/api\/sessions\/[^/]+\/turns$/.test(response.url())
      && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  const sourceResponse = await sourceAdmission;
  expect(sourceResponse.status()).toBe(202);
  const sourceTurn = await sourceResponse.json();
  let sourceTaskId = "";
  await expect.poll(async () => {
    const saved = await (await page.request.get(
      `/api/sessions/${sourceTurn.session_id}/turns/${sourceTurn.request_id}`,
    )).json();
    expect(saved.status).not.toBe("failed");
    sourceTaskId = saved.linked_task_id ?? "";
    return saved.status;
  }).toBe("completed");
  expect(sourceTaskId).toMatch(/^[0-9a-f-]{36}$/);
  await expect.poll(async () => {
    const saved = await (await page.request.get(`/api/tasks/${sourceTaskId}`)).json();
    return saved.snapshot.runtime_status;
  }).toBe("completed");
  await expect(page.getByLabel("关联已有任务（可选）")).toHaveValue(sourceTaskId);

  const geometryDraft = "对原图像素框 [1, 1, 8, 8] 做分割和相对量化";
  await page.getByLabel("对话内容", { exact: true }).fill(geometryDraft);
  const newAdmission = page.waitForResponse((response) =>
    new URL(response.url()).pathname === "/api/tasks"
      && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "用原图新建复检", exact: true }).click();
  const newResponse = await newAdmission;
  expect(newResponse.status()).toBe(201);
  const newTask = await newResponse.json();
  const newTaskId = newTask.snapshot.task_id as string;
  expect(newTaskId).not.toBe(sourceTaskId);

  const sourceStored = await (await page.request.get(`/api/tasks/${sourceTaskId}`)).json();
  const newStored = await (await page.request.get(`/api/tasks/${newTaskId}`)).json();
  expect(sourceStored.inspection_mode).toBe("yolo_only");
  expect(newStored.inspection_mode).toBe("yolo_vlm");
  expect(newStored.input_asset).toEqual(sourceStored.input_asset);
  expect(newStored.parent_task_id ?? null).toBeNull();
  await expect(page.getByLabel("关联已有任务（可选）")).toHaveValue(newTaskId);
  await expect(page.getByLabel("对话内容", { exact: true })).toHaveValue(geometryDraft);
  await expect(page.getByRole("button", { name: "发送消息", exact: true })).toBeDisabled();
  await expect(page.getByText(/当前草稿已保留/)).toBeVisible();

  await review(page, newTaskId);
  await expect(page.getByRole("button", { name: "发送消息", exact: true })).toBeEnabled();
  await expect(page.getByLabel("对话内容", { exact: true })).toHaveValue(geometryDraft);
  await expect(page.getByLabel("关联已有任务（可选）")).toHaveValue(newTaskId);
  await page.screenshot({
    path: path.join(output, "screenshots/07-guided-recovery.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("image/video dialogue, durable review, evidence reports and saved comparison", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await mkdir(path.join(output, "screenshots"), { recursive: true });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "管道影像检查" })).toBeVisible();
  const first = await inspect(page, "image.png", "浏览器验收图片一：请检查并复核");
  await expect(page.getByLabel("任务与证据")).not.toBeVisible();
  await expect(page.locator(".turn-card").filter({ hasText: "浏览器验收图片一" })).toBeVisible();
  await page.screenshot({
    path: path.join(output, "screenshots/00-conversation-default-details-closed.png"),
    fullPage: true,
  });
  await openTaskDetails(page);
  await expect(card(page, first).getByRole("button", { name: "开始人工复核" })).toBeVisible();
  await page.reload();
  await openTaskDetails(page);
  await expect(card(page, first).getByRole("button", { name: "开始人工复核" })).toBeVisible();
  await page.screenshot({ path: path.join(output, "screenshots/01-image-waiting.png"), fullPage: true });
  await review(page, first);
  await page.screenshot({ path: path.join(output, "screenshots/02-image-reviewed.png"), fullPage: true });

  const video = await inspect(page, "video.mp4", "浏览器验收短视频：请检查并复核");
  await review(page, video);
  const videoCard = card(page, video);
  await videoCard.getByRole("button", { name: "展开视频候选时间线" }).click();
  await expect(videoCard.getByText("解码完整", { exact: true })).toBeVisible();
  await expect(videoCard.getByRole("heading", { name: "复检证据选择", exact: true })).toBeVisible();
  await expect(videoCard.getByText(/入选不代表复检已经完成/)).toBeVisible();
  await expect(videoCard.locator(".timeline-events > li")).toHaveCount(1);
  await expect(videoCard.getByText(/帧 0–5 · 6 次观测/)).toBeVisible();
  await expect(videoCard.getByRole("link", { name: /证据帧/ }).first()).toBeVisible();
  await page.screenshot({ path: path.join(output, "screenshots/03-video-evidence.png"), fullPage: true });

  const videoDetail = await (await page.request.get(`/api/tasks/${video}/video`)).json();
  const selectedFrame = videoDetail.evidence.frames.find((frame: { frame_index: number }) =>
    frame.frame_index === videoDetail.scan.state.events[0].evidence[0].frame_index,
  );
  expect(selectedFrame).toBeTruthy();
  await videoCard.getByText(`选择已存证据帧（${videoDetail.evidence.frames.length}）`, {
    exact: true,
  }).click();
  await videoCard.getByRole("button", {
    name: `选择证据帧 ${selectedFrame.frame_index} 用于工具`, exact: true,
  }).first().click();
  await expect(page.getByLabel("已选视频证据帧", { exact: true })).toBeVisible();
  await expect(page.getByLabel("关联已有任务（可选）")).toHaveValue(video);
  await page.screenshot({ path: path.join(output, "screenshots/06-selected-video-frame.png"), fullPage: true });
  // This synthetic browser journey checks the exact reference and safe boundary;
  // actual frame enhancement/geometry execution has its own PG/Temporal integration test.
  await page.getByLabel("对话内容", { exact: true }).fill("询问所选视频证据帧状态");
  const frameAdmission = page.waitForResponse((response) =>
    /\/api\/sessions\/[^/]+\/turns$/.test(response.url()) && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  const frameResponse = await frameAdmission;
  expect(frameResponse.status()).toBe(202);
  const frameTurn = await frameResponse.json();
  expect(frameTurn.task_id).toBe(video);
  expect(frameTurn.input_asset).toEqual(selectedFrame.asset);
  await expect.poll(async () => {
    const saved = await (await page.request.get(
      `/api/sessions/${frameTurn.session_id}/turns/${frameTurn.request_id}`,
    )).json();
    expect(saved.status).not.toBe("failed");
    return saved.reply;
  }).toContain("所选帧入口仅支持");

  const second = await inspect(page, "image.png", "浏览器验收图片二：请检查并复核");
  await review(page, second);
  await openOptions(page);
  await page.getByLabel("关联已有任务（可选）").selectOption(second);
  await page.getByLabel("对比参考任务（可选）").selectOption(first);
  await page.getByLabel("对话内容", { exact: true }).fill("比较这两份已保存检查结果并导出报告");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  const comparisonTurn = page.locator(".turn-card").filter({ hasText: "比较这两份已保存检查结果并导出报告" });
  await comparisonTurn.getByRole("button", { name: "展开保存结果对比" }).click();
  await expect(comparisonTurn.getByText("AJ · 支管暗接")).toBeVisible();
  const jsonLink = comparisonTurn.getByRole("link", { name: "JSON 对比报告" });
  const comparison = await (await page.request.get((await jsonLink.getAttribute("href"))!)).json();
  expect(comparison.reference.snapshot.task_id).toBe(first);
  expect(comparison.current.snapshot.task_id).toBe(second);
  expect(comparison.rows[0]).toMatchObject({
    label: "AJ", reference: { confirmed: 1 }, current: { confirmed: 1 }, confirmed_delta: 0,
  });
  await writeFile(path.join(output, "comparison.json"), JSON.stringify(comparison, null, 2));
  await page.screenshot({ path: path.join(output, "screenshots/04-comparison.png"), fullPage: true });
  const popupPromise = page.waitForEvent("popup");
  await comparisonTurn.getByRole("link", { name: "HTML 对比报告" }).click();
  const popup = await popupPromise;
  await expect(popup.getByRole("heading", { name: "已保存检查结果对比", exact: true })).toBeVisible();
  await popup.screenshot({ path: path.join(output, "screenshots/05-html-report.png"), fullPage: true });
  const sessionId = await page.evaluate(() => localStorage.getItem("pipe-inspector.session-id"));
  const unifiedResponse = await page.request.get(`/api/sessions/${sessionId}/report?format=json`);
  expect(unifiedResponse.status()).toBe(200);
  const unified = await unifiedResponse.json();
  expect(unified.session.session_id).toBe(sessionId);
  expect(unified.asset_groups.length).toBeGreaterThanOrEqual(2);
  expect(unified.asset_groups.flatMap((group: { sources: { task_id: string }[] }) =>
    group.sources.map((source) => source.task_id))).toEqual(expect.arrayContaining([first, second, video]));
  const unifiedPopupPromise = page.waitForEvent("popup");
  await page.getByText("统一报告", { exact: true }).click();
  await page.getByRole("link", { name: "HTML 统一报告" }).click();
  const unifiedPopup = await unifiedPopupPromise;
  await expect(unifiedPopup.getByRole("heading", { name: "会话统一检查报告" })).toBeVisible();
  await writeFile(path.join(output, "session-report.json"), JSON.stringify(unified, null, 2));
  expect(errors).toEqual([]);
  await writeFile(path.join(output, "browser-evidence.json"), JSON.stringify({
    image_tasks: [first, second], video_task: video, synthetic_models: true,
    selected_frame: selectedFrame, selected_frame_turn: frameTurn.request_id,
    page_errors: errors, real_browser_http_postgres_temporal: true,
  }, null, 2));
});
