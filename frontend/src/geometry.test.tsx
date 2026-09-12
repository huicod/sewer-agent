import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, getGeometry } from "./api";
import { GeometryPanel } from "./GeometryPanel";
import type { AssetRecord, AssetRef, GeometryDetail } from "./types";

const taskId = "44444444-4444-4444-8444-444444444444";
const otherTaskId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const findingId = "55555555-5555-4555-8555-555555555555";
const source: AssetRef = { asset_id: "11111111-1111-4111-8111-111111111111", version: 1 };

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function asset<K extends "mask" | "depth_map">(
  id: string,
  kind: K,
  mediaType: string,
): AssetRecord & { kind: K; width: number; height: number; parent: AssetRef } {
  return {
    ref: { asset_id: id, version: 1 },
    kind,
    media_type: mediaType,
    byte_size: 128,
    width: 8,
    height: 6,
    duration_seconds: null,
    parent: source,
  };
}

function detail(id = taskId): GeometryDetail {
  return {
    task_id: id,
    segmentation: {
      schema_version: "1",
      input_asset: source,
      prompt: { width: 8, height: 6, bbox_xyxy: [2, 1, 7, 5] },
      mask_asset: asset("22222222-2222-4222-8222-222222222222", "mask", "image/png"),
      overlay_asset: asset("33333333-3333-4333-8333-333333333333", "mask", "image/png"),
      model_id: "facebook/sam2.1-hiera-large",
      model_version: "sam-pinned-v1",
      model_score: 0.81,
      area_pixels: 6,
      mask_region: { width: 8, height: 6, bbox_xyxy: [3, 2, 6, 4] },
      review_required: true,
    },
    depth: {
      schema_version: "1",
      input_asset: source,
      raw_asset: asset(
        "66666666-6666-4666-8666-666666666666",
        "depth_map",
        "application/x-npy",
      ),
      preview_asset: asset(
        "77777777-7777-4777-8777-777777777777",
        "depth_map",
        "image/png",
      ),
      model_id: "depth-anything/Depth-Anything-V2-Small-hf",
      model_version: "depth-pinned-v1",
      larger_is: "nearer",
      raw_min: 0,
      raw_max: 7,
      units: "relative_model_units",
      preview_mapping: "per_image_minmax_white_near",
      review_required: true,
    },
    measurements: {
      input_asset: source,
      area_pixels: 6,
      image_plane_fraction: 0.125,
      fraction_denominator: "full_image_pixels",
      bbox_width_pixels: 3,
      bbox_height_pixels: 2,
      relative_depth: {
        q10: 3,
        median: 4,
        q90: 5,
        larger_is: "nearer",
        units: "relative_model_units",
      },
      metric_status: "unavailable_no_calibration",
      review_required: true,
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("geometry HTTP boundary", () => {
  it("accepts complete same-task geometry evidence", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(detail()));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getGeometry(taskId)).resolves.toEqual(detail());
    expect(fetchMock).toHaveBeenCalledWith(`/api/tasks/${taskId}/geometry`, undefined);
  });

  it.each([
    ["wrong task", (payload: GeometryDetail) => { payload.task_id = otherTaskId; }],
    ["wrong mask parent", (payload: GeometryDetail) => {
      payload.segmentation!.mask_asset.parent = { asset_id: otherTaskId, version: 1 };
    }],
    ["wrong measurement area", (payload: GeometryDetail) => {
      payload.measurements!.area_pixels = 7;
    }],
    ["unordered relative depth", (payload: GeometryDetail) => {
      payload.measurements!.relative_depth!.q10 = 6;
    }],
    ["private checkpoint path", (payload: GeometryDetail) => {
      (payload.depth as unknown as Record<string, unknown>).checkpoint = "/private/model";
    }],
  ])("rejects %s", async (_name, mutate) => {
    const payload = structuredClone(detail());
    mutate(payload);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(payload)));

    await expect(getGeometry(taskId)).rejects.toEqual(expect.objectContaining<ApiError>({
      name: "ApiError",
      status: 200,
      message: "服务返回的数据格式无效",
    }));
  });
});

describe("geometry evidence panel", () => {
  it("loads on demand and renders original coordinates, assets, and bounded units", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(detail()));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(
      <GeometryPanel
        taskId={taskId}
        snapshotRevision="2026-09-13T12:00:00Z"
        runtimeStatus="partial"
        findingIds={[findingId]}
      />,
    );
    expect(fetchMock).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "展开几何证据" }));

    const original = await screen.findByRole("img", { name: "原图与提示框" });
    expect(original.getAttribute("viewBox")).toBe("0 0 8 6");
    expect(original.querySelector("image")?.getAttribute("href")).toBe(
      `/api/assets/${source.asset_id}/versions/1/content`,
    );
    const prompt = original.querySelector("rect");
    expect([
      prompt?.getAttribute("x"),
      prompt?.getAttribute("y"),
      prompt?.getAttribute("width"),
      prompt?.getAttribute("height"),
    ]).toEqual(["2", "1", "5", "4"]);
    expect(screen.getByRole("img", { name: "二值预测掩膜" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "掩膜叠加图" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "相对深度预览，白近黑远" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /NPY/ }).getAttribute("href")).toContain(
      "66666666-6666-4666-8666-666666666666",
    );
    expect(screen.getByText(/掩膜面积 6 像素/).textContent).toContain("占全图像素 12.5%");
    expect(screen.getByText(/q10 3.*中位数 4.*q90 5/).textContent).toContain("相对模型单位");
    expect(screen.getByText(/白色=近，黑色=远/)).toBeTruthy();
    expect(screen.getByText(/质量估计 0.81.*不是缺陷置信度/)).toBeTruthy();
    expect(screen.getByText(new RegExp(findingId))).toBeTruthy();
    expect(screen.queryByText(/毫米|堵塞率|准确率/)).toBeNull();
  });

  it("distinguishes an API failure from a successful empty detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      detail: { code: "storage_unavailable", message: "几何证据暂不可用" },
    }, 503)));
    const user = userEvent.setup();
    const { rerender } = render(
      <GeometryPanel
        taskId={taskId}
        snapshotRevision="r1"
        runtimeStatus="completed"
        findingIds={[]}
      />,
    );
    await user.click(screen.getByRole("button", { name: "展开几何证据" }));
    expect((await screen.findByRole("alert")).textContent).toContain("几何证据暂不可用");

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      task_id: otherTaskId,
      segmentation: null,
      depth: null,
      measurements: null,
    })));
    rerender(
      <GeometryPanel
        taskId={otherTaskId}
        snapshotRevision="r2"
        runtimeStatus="completed"
        findingIds={[]}
      />,
    );
    await user.click(screen.getByRole("button", { name: "展开几何证据" }));
    expect(await screen.findByText("该任务没有已存的几何工具结果。")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("discards stale responses after task switch and after closing", async () => {
    let resolveFirst!: (value: Response) => void;
    let resolveSecond!: (value: Response) => void;
    const first = new Promise<Response>((resolve) => { resolveFirst = resolve; });
    const second = new Promise<Response>((resolve) => { resolveSecond = resolve; });
    vi.stubGlobal("fetch", vi.fn().mockReturnValueOnce(first).mockReturnValueOnce(second));
    const user = userEvent.setup();
    const { rerender } = render(
      <GeometryPanel taskId={taskId} snapshotRevision="r1" runtimeStatus="running" findingIds={[]} />,
    );
    await user.click(screen.getByRole("button", { name: "展开几何证据" }));
    rerender(
      <GeometryPanel taskId={otherTaskId} snapshotRevision="r2" runtimeStatus="running" findingIds={[]} />,
    );
    resolveFirst(response(detail()));
    await waitFor(() => expect(screen.queryByRole("img", { name: "二值预测掩膜" })).toBeNull());

    await user.click(screen.getByRole("button", { name: "展开几何证据" }));
    await user.click(screen.getByRole("button", { name: "收起几何证据" }));
    resolveSecond(response(detail(otherTaskId)));
    await waitFor(() => expect(screen.queryByRole("img", { name: "二值预测掩膜" })).toBeNull());
  });
});
