import { expect, test, type Locator, type Page } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import type { AssetRef, TaskRecord } from "../src/types";

// Paid API + GPU opt-in only. Never submit a human decision for real CCTV.
const output = process.env.SEWER_BROWSER_OUTPUT!;
const save = (name: string, value: unknown) => writeFile(
  path.join(output, name), JSON.stringify(value, null, 2),
);

async function read(page: Page, url: string) {
  const response = await page.request.get(url);
  expect(response.status(), url).toBe(200);
  return response.json();
}

function card(page: Page, taskId: string): Locator {
  return page.locator(".task-card").filter({ has: page.locator(`[title="${taskId}"]`) });
}

async function inspect(page: Page, name: string, instruction: string): Promise<TaskRecord> {
  const started = performance.now();
  await page.getByLabel("对话附件（可选）").setInputFiles(
    path.join(output, "service/fixtures/image.jpg"),
  );
  await page.getByLabel("对话内容", { exact: true }).fill(instruction);
  const submitted = page.waitForResponse((response) =>
    /\/api\/sessions\/[^/]+\/turns$/.test(response.url()) && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  const response = await submitted;
  expect(response.status()).toBe(202);
  const admitted = await response.json();
  let taskId: string | null = null;
  await expect.poll(async () => {
    const turn = await read(page, `/api/sessions/${admitted.session_id}/turns/${admitted.request_id}`);
    await save(`${name}-turn.json`, turn);
    taskId = turn.linked_task_id;
    return turn.status;
  }, { timeout: 180_000 }).toMatch(/^(completed|failed)$/);
  expect(taskId, "Actual LLM must admit the requested inspection").toMatch(/^[0-9a-f-]{36}$/);

  const stages: unknown[] = [];
  let lastMarker = "";
  await expect.poll(async () => {
    const task: TaskRecord = await read(page, `/api/tasks/${taskId}`);
    const snapshot = task.snapshot;
    const marker = `${snapshot.runtime_status}:${snapshot.stage}:${snapshot.active_tool}`;
    if (marker !== lastMarker) {
      stages.push({ elapsed_seconds: (performance.now() - started) / 1000, snapshot });
      await save(`${name}-stages.json`, stages);
      lastMarker = marker;
    }
    return snapshot.runtime_status;
  }, { timeout: 300_000 }).toMatch(/^(completed|partial|waiting_review|failed|cancelled)$/);
  const task: TaskRecord = await read(page, `/api/tasks/${taskId}`);
  await save(`${name}-task.json`, task);
  expect(["completed", "partial", "waiting_review"]).toContain(task.snapshot.runtime_status);
  expect(task.snapshot.findings.every((finding) => finding.confirmed_by !== "human")).toBe(true);
  await expect(card(page, task.snapshot.task_id)).toBeVisible();
  return task;
}

async function evidence(page: Page, ref: AssetRef, filename: string) {
  const response = await page.request.get(`/api/assets/${ref.asset_id}/versions/${ref.version}/content`);
  expect(response.status(), filename).toBe(200);
  const body = await response.body();
  expect(body.length).toBeGreaterThan(0);
  await writeFile(path.join(output, filename), body);
}

async function loaded(image: Locator) {
  await image.scrollIntoViewIfNeeded();
  await expect.poll(() => image.evaluate((element) =>
    (element as HTMLImageElement).complete && (element as HTMLImageElement).naturalWidth > 0,
  )).toBe(true);
}

async function callsAndReport(page: Page, name: string, task: TaskRecord, steps: string[]) {
  const calls: { step_id: string; status: string }[] = await read(page, `/api/tasks/${task.snapshot.task_id}/calls`);
  await save(`${name}-calls.json`, calls);
  for (const step of steps) {
    expect(calls.find((call) => call.step_id === step)?.status, step).toBe("succeeded");
  }
  const response = await page.request.get(`/api/tasks/${task.snapshot.task_id}/report?format=json`);
  if (task.snapshot.runtime_status === "waiting_review") {
    expect(response.status()).toBe(409);
    const detail = await read(page, `/api/tasks/${task.snapshot.task_id}/review`);
    expect(detail.record).toBeNull();
    await save(`${name}-pending-review.json`, detail);
  } else {
    expect(response.status()).toBe(200);
    await save(`${name}-report.json`, await response.json());
  }
}

function watch(page: Page) {
  const errors: string[] = [];
  const reviewWrites: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (request.method() === "POST" && /\/review(?:\?|$)/.test(request.url())) {
      reviewWrites.push(request.url());
    }
  });
  return { errors, reviewWrites };
}

async function finish(name: string, task: TaskRecord, observed: ReturnType<typeof watch>) {
  expect(observed.reviewWrites).toEqual([]);
  expect(observed.errors).toEqual([]);
  await save(`${name}-browser-evidence.json`, {
    image_tasks: [task.snapshot.task_id], source_samples: 1, synthetic_models: false,
    human_review_submissions: observed.reviewWrites, page_errors: observed.errors,
    real_browser_http_postgres_temporal: true,
  });
}

test("real CCTV geometry and original review evidence", async ({ page }) => {
  const observed = watch(page);
  await mkdir(path.join(output, "screenshots"), { recursive: true });
  await page.goto("/");
  const first = await inspect(page, "geometry", "新建图片检查，先完成五类检测和 VLM 复检，然后对原图像素框"
    + "[280,66,549,350]执行分割和相对量化（relative_measure），"
    + "给出掩膜、像素面积和该掩膜内的相对深度统计。不要增强图片，不测实际毫米尺寸。");
  await callsAndReport(page, "geometry", first, ["detect", "verify", "geometry_segment", "geometry_depth"]);
  const geometry = await read(page, `/api/tasks/${first.snapshot.task_id}/geometry`);
  await save("geometry.json", geometry);
  expect(geometry.segmentation.prompt.bbox_xyxy).toEqual([280, 66, 549, 350]);
  expect(geometry.measurements.area_pixels).toBeGreaterThan(0);
  expect(geometry.measurements.relative_depth).not.toBeNull();
  expect(geometry.measurements.metric_status).toBe("unavailable_no_calibration");
  await evidence(page, first.input_asset, "original.jpg");
  await evidence(page, geometry.segmentation.mask_asset.ref, "mask.png");
  await evidence(page, geometry.segmentation.overlay_asset.ref, "overlay.png");
  await evidence(page, geometry.depth.preview_asset.ref, "depth.png");
  await evidence(page, geometry.depth.raw_asset.ref, "depth.npy");
  await page.reload();
  const firstCard = card(page, first.snapshot.task_id);
  await firstCard.getByRole("button", { name: "展开几何证据", exact: true }).click();
  await loaded(firstCard.getByAltText("二值预测掩膜"));
  await loaded(firstCard.getByAltText("掩膜叠加图"));
  await loaded(firstCard.getByAltText("相对深度预览，白近黑远"));
  await firstCard.screenshot({ path: path.join(output, "screenshots/01-geometry.png") });
  expect(await read(page, `/api/tasks/${first.snapshot.task_id}/geometry`)).toEqual(geometry);
  if (first.snapshot.runtime_status === "waiting_review") {
    await firstCard.getByRole("button", { name: "开始人工复核" }).click();
    await expect(firstCard.getByRole("heading", { name: "人工复核", exact: true })).toBeVisible();
    await firstCard.locator(".geometry-panel").getByRole("button").first().click();
    await firstCard.screenshot({ path: path.join(output, "screenshots/02-original-review.png") });
  }
  await finish("geometry", first, observed);
});

test("real CCTV ROI enhancement evidence", async ({ page }) => {
  const observed = watch(page);
  await mkdir(path.join(output, "screenshots"), { recursive: true });
  await page.goto("/");
  const second = await inspect(page, "enhancement", "新建图片检查，先完成五类检测和VLM复检，再对原图像素区域"
    + "[150,50,406,306]执行局部超分，并辅助复核。不要执行其他增强。");
  await callsAndReport(page, "enhancement", second, ["detect", "verify", "enhance", "enhancement_review"]);
  const enhancement = await read(page, `/api/tasks/${second.snapshot.task_id}/enhancement`);
  await save("enhancement.json", enhancement);
  expect(enhancement.result.kind).toBe("super_resolution");
  expect(enhancement.result.source_region.bbox_xyxy).toEqual([150, 50, 406, 306]);
  expect(enhancement.review).not.toBeNull();
  await evidence(page, enhancement.result.output_asset.ref, "enhanced.png");
  await page.reload();
  const secondCard = card(page, second.snapshot.task_id);
  await secondCard.getByRole("button", { name: "展开图片增强对照", exact: true }).click();
  await loaded(secondCard.getByAltText("增强派生图"));
  await expect(secondCard.getByRole("heading", { name: "辅助复核", exact: true })).toBeVisible();
  await secondCard.screenshot({ path: path.join(output, "screenshots/03-enhancement.png") });
  expect(await read(page, `/api/tasks/${second.snapshot.task_id}/enhancement`)).toEqual(enhancement);
  await finish("enhancement", second, observed);
});
