import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, getEnhancement } from "./api";
import { EnhancementPanel } from "./EnhancementPanel";
import type { EnhancementDetail } from "./types";

const taskId = "44444444-4444-4444-8444-444444444444";
const otherTaskId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const sourceAssetId = "11111111-1111-4111-8111-111111111111";
const outputAssetId = "77777777-7777-4777-8777-777777777777";

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function detail(id = taskId): EnhancementDetail {
  const input = { asset_id: sourceAssetId, version: 1 };
  return {
    task_id: id,
    selection: null,
    result: {
      schema_version: "1",
      input_asset: input,
      output_asset: {
        ref: { asset_id: outputAssetId, version: 1 },
        kind: "enhanced_image",
        media_type: "image/png",
        byte_size: 2048,
        width: 1280,
        height: 960,
        duration_seconds: null,
        parent: input,
      },
      kind: "super_resolution",
      model_id: "swinir",
      model_version: "v1",
      source_region: { width: 640, height: 480, bbox_xyxy: [64, 48, 320, 240] },
      quality_before: {
        mean_luminance: 36,
        contrast: 12,
        sharpness: 7,
        adequate: false,
        reasons: ["清晰度不足"],
      },
      quality_after: {
        mean_luminance: 40,
        contrast: 18,
        sharpness: 16,
        adequate: true,
        reasons: [],
      },
      review_required: true,
    },
    review: {
      quality_effect: "improved",
      conflicting_finding_ids: ["55555555-5555-4555-8555-555555555555"],
      additional_anomalies: [{
        label: "OTHER",
        name: "疑似沉积",
        bbox_xyxy: [80, 60, 140, 120],
        reason: "增强图中出现需人工确认的区域",
      }],
      summary: "辅助复核发现画面更易辨认，但不替代原结论。",
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("enhancement HTTP boundary", () => {
  it("accepts the complete stored detail and requests only the selected task", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(detail()));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getEnhancement(taskId)).resolves.toEqual(detail());
    expect(fetchMock).toHaveBeenCalledWith(`/api/tasks/${taskId}/enhancement`, undefined);
  });

  it.each([
    ["wrong output parent", (payload: EnhancementDetail) => {
      payload.result!.output_asset.parent = { asset_id: otherTaskId, version: 1 };
    }],
    ["unknown quality effect", (payload: EnhancementDetail) => {
      (payload.review as unknown as { quality_effect: string }).quality_effect = "perfect";
    }],
    ["out-of-bounds source ROI", (payload: EnhancementDetail) => {
      payload.result!.source_region.bbox_xyxy = [64, 48, 700, 240];
    }],
    ["undocumented server path", (payload: EnhancementDetail) => {
      (payload.result!.input_asset as unknown as Record<string, unknown>).server_path = "/srv/private/input.png";
    }],
  ])("rejects %s", async (_name, mutate) => {
    const payload = structuredClone(detail());
    mutate(payload);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(payload)));

    await expect(getEnhancement(taskId)).rejects.toEqual(expect.objectContaining<ApiError>({
      name: "ApiError",
      status: 200,
      message: "服务返回的数据格式无效",
    }));
  });

  it("rejects a valid detail returned for another task", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(detail(otherTaskId))));

    await expect(getEnhancement(taskId)).rejects.toEqual(expect.objectContaining<ApiError>({
      name: "ApiError",
      status: 200,
      message: "服务返回的数据格式无效",
    }));
  });

  it("accepts an older detail response with no selection field", async () => {
    const legacy = structuredClone(detail()) as EnhancementDetail & { selection?: unknown };
    delete legacy.selection;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(legacy)));

    await expect(getEnhancement(taskId)).resolves.toEqual(legacy);
  });

  it("rejects a selection that contains private execution configuration", async () => {
    const payload = structuredClone(detail());
    payload.selection = {
      status: "selected",
      reason: "低照度影响判读",
      proposal: {
        options: { kind: "low_light", roi_xyxy: null },
        degradation: "low_light",
        confidence: "clear",
        evidence: "管壁区域整体偏暗",
        inspection_impact: "暗部细节难以辨认",
      },
      options: { kind: "low_light", roi_xyxy: null },
    };
    (payload.selection as unknown as Record<string, unknown>).model_path = "/srv/models/private.pt";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(payload)));

    await expect(getEnhancement(taskId)).rejects.toEqual(expect.objectContaining<ApiError>({
      name: "ApiError",
      status: 200,
      message: "服务返回的数据格式无效",
    }));
  });
});

describe("enhancement comparison panel", () => {
  it("loads on demand and renders the exact original ROI beside the derived image", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(detail()));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<EnhancementPanel taskId={taskId} snapshotRevision="2026-09-12T12:00:00Z" runtimeStatus="waiting_review" />);
    expect(fetchMock).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "展开图片增强对照" }));

    const original = await screen.findByRole("img", { name: "原图与处理范围" });
    expect(original.getAttribute("viewBox")).toBe("0 0 640 480");
    expect(original.querySelector("image")?.getAttribute("href")).toBe(
      `/api/assets/${sourceAssetId}/versions/1/content`,
    );
    const roi = original.querySelector("rect");
    expect([roi?.getAttribute("x"), roi?.getAttribute("y"), roi?.getAttribute("width"), roi?.getAttribute("height")])
      .toEqual(["64", "48", "256", "192"]);
    expect(screen.getByRole("img", { name: "增强派生图" }).getAttribute("src")).toBe(
      `/api/assets/${outputAssetId}/versions/1/content`,
    );
    expect(screen.getByText(/swinir · v1/)).toBeTruthy();
    expect(screen.getByText(/原图 ROI.*实际处理范围/)).toBeTruthy();
    expect(screen.getByText(/辅助复核发现画面更易辨认/)).toBeTruthy();
  });

  it("shows a load failure without presenting enhancement as successful", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      detail: { code: "storage_unavailable", message: "增强证据暂不可用" },
    }, 503)));
    const user = userEvent.setup();
    render(<EnhancementPanel taskId={taskId} snapshotRevision="2026-09-12T12:00:00Z" runtimeStatus="completed" />);

    await user.click(screen.getByRole("button", { name: "展开图片增强对照" }));
    expect((await screen.findByRole("alert")).textContent).toContain("增强证据暂不可用");
    expect(screen.queryByRole("img", { name: "增强派生图" })).toBeNull();
  });

  it("invalidates an old request when switching tasks", async () => {
    let resolveFirst!: (value: Response) => void;
    const first = new Promise<Response>((resolve) => { resolveFirst = resolve; });
    const fetchMock = vi.fn()
      .mockReturnValueOnce(first)
      .mockResolvedValueOnce(response({ task_id: otherTaskId, result: null, review: null }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const { rerender } = render(
      <EnhancementPanel taskId={taskId} snapshotRevision="2026-09-12T12:00:00Z" runtimeStatus="running" />,
    );
    await user.click(screen.getByRole("button", { name: "展开图片增强对照" }));

    rerender(<EnhancementPanel taskId={otherTaskId} snapshotRevision="2026-09-12T12:01:00Z" runtimeStatus="running" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "展开图片增强对照" }).getAttribute("aria-expanded")).toBe("false"));
    resolveFirst(response(detail()));
    await waitFor(() => expect(screen.queryByRole("img", { name: "增强派生图" })).toBeNull());

    await user.click(screen.getByRole("button", { name: "展开图片增强对照" }));
    expect(await screen.findByText("正在处理，暂未生成持久化增强结果。")).toBeTruthy();
    expect(fetchMock.mock.calls[1]?.[0]).toBe(`/api/tasks/${otherTaskId}/enhancement`);
  });

  it("reads persisted content again after collapse and reopen", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(response(detail())));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<EnhancementPanel taskId={taskId} snapshotRevision="2026-09-12T12:00:00Z" runtimeStatus="completed" />);

    await user.click(screen.getByRole("button", { name: "展开图片增强对照" }));
    await screen.findByRole("img", { name: "增强派生图" });
    await user.click(screen.getByRole("button", { name: "收起图片增强对照" }));
    expect(screen.queryByRole("img", { name: "增强派生图" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "展开图片增强对照" }));
    await screen.findByRole("img", { name: "增强派生图" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("shows a skipped adaptive selection reason without inventing an output image", async () => {
    const payload: EnhancementDetail = {
      task_id: taskId,
      selection: {
        status: "skipped",
        reason: "建议置信度不足，保持原图证据",
        proposal: {
          options: null,
          degradation: "mixed_dark_noise_blur",
          confidence: "uncertain",
          evidence: "暗部与噪声同时存在",
          inspection_impact: "无法确定单一工具能改善判读",
        },
        options: null,
      },
      result: null,
      review: null,
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(payload)));
    const user = userEvent.setup();
    render(<EnhancementPanel taskId={taskId} snapshotRevision="2026-09-13T10:00:00Z" runtimeStatus="completed" />);

    await user.click(screen.getByRole("button", { name: "展开图片增强对照" }));

    expect(await screen.findByText(/自动增强已跳过.*建议置信度不足/)).toBeTruthy();
    expect(screen.getByText(/暗部与噪声同时存在/)).toBeTruthy();
    expect(screen.queryByRole("img", { name: "增强派生图" })).toBeNull();
  });

  it("shows a selected low-light reason while the output is still pending", async () => {
    const payload: EnhancementDetail = {
      task_id: taskId,
      selection: {
        status: "selected",
        reason: "低照度清晰影响判读，允许执行恢复",
        proposal: {
          options: { kind: "low_light", roi_xyxy: null },
          degradation: "low_light",
          confidence: "clear",
          evidence: "管壁暗部细节缺失",
          inspection_impact: "裂纹边缘难以辨认",
        },
        options: { kind: "low_light", roi_xyxy: null },
      },
      result: null,
      review: null,
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(payload)));
    const user = userEvent.setup();
    render(<EnhancementPanel taskId={taskId} snapshotRevision="2026-09-13T10:01:00Z" runtimeStatus="running" />);

    await user.click(screen.getByRole("button", { name: "展开图片增强对照" }));

    expect(await screen.findByText(/自动增强已选择.*低照度恢复/)).toBeTruthy();
    expect(screen.getByText(/增强结果尚未生成/)).toBeTruthy();
    expect(screen.queryByRole("img", { name: "增强派生图" })).toBeNull();
  });
});
