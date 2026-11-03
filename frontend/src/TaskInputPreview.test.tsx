import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as api from "./api";
import { TaskInputPreview } from "./TaskInputPreview";
import type { AssetRecord, EvidenceRef, Finding, TaskRecord } from "./types";

const input = { asset_id: "11111111-1111-4111-8111-111111111111", version: 1 };
const task: TaskRecord = {
  session_id: "22222222-2222-4222-8222-222222222222",
  request_id: "33333333-3333-4333-8333-333333333333",
  created_at: "2026-11-02T12:00:00Z",
  input_asset: input,
  instruction: "检查现场影像",
  snapshot: {
    task_id: "44444444-4444-4444-8444-444444444444", schema_version: "1",
    runtime_status: "waiting_review", inspection_result: "inconclusive", stage: "review",
    stage_progress: 1, overall_progress: 0.9, processed_chunks: 1, processed_frames: 1,
    retry_count: 0, waiting_reason: null, active_tool: null, updated_at: "2026-11-02T12:01:00Z",
    coverage: { sufficient_coverage: true, sufficient_quality: true, negative_audit_complete: false },
    findings: [],
  },
};
const asset: AssetRecord = {
  ref: input, kind: "image", media_type: "image/jpeg", byte_size: 1024,
  width: 640, height: 480, parent: null, duration_seconds: null,
};
const evidence: EvidenceRef = {
  asset: input, region: { width: 640, height: 480, bbox_xyxy: [20, 40, 100, 160] },
  timestamp_seconds: null, frame_index: null,
};
function finding(id: string, item: EvidenceRef, status: Finding["review_status"] = "needs_review"): Finding {
  return {
    finding_id: id, task_id: task.snapshot.task_id, canonical_label: "PL", open_label: null,
    confirmed_by: null, score: 0.56, source: "detector", review_status: status, evidence: [item],
  };
}
const url = `/api/assets/${input.asset_id}/versions/1/content`;

describe("task input preview", () => {
  beforeEach(() => vi.stubGlobal("IntersectionObserver", undefined));
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it("shows the saved input image and only draws findings located on that image", async () => {
    vi.spyOn(api, "getAsset").mockResolvedValue(asset);
    const findings = [
      finding("matching", evidence),
      { ...finding("rule-confirmed", evidence, "confirmed"), confirmed_by: "detector_only_rules" as const },
      finding("rejected", evidence, "rejected"),
      finding("different-version", { ...evidence, asset: { ...input, version: 2 } }),
      finding("different-size", { ...evidence, region: { ...evidence.region!, width: 800 } }),
    ];
    const { container } = render(<TaskInputPreview task={{ ...task, snapshot: { ...task.snapshot, findings } }} />);
    const img = await screen.findByRole("img", { name: "检查任务的输入图片" });
    expect(img.getAttribute("src")).toBe(url);
    expect(screen.getByRole("link", { name: "打开原图" }).getAttribute("href")).toBe(url);
    const boxes = container.querySelectorAll("svg rect");
    expect(boxes).toHaveLength(2);
    expect(boxes[0].getAttribute("x")).toBe("20");
    expect(boxes[0].getAttribute("height")).toBe("120");
    expect(screen.getAllByText("PL · 破裂")).toHaveLength(1);
    expect(screen.getByText("× 2")).toBeTruthy();
    expect(screen.getByText("需复核 1 项、纯 YOLO 规则确认 1 项")).toBeTruthy();
    fireEvent.error(img);
    expect(screen.getByText(/预览暂不可用/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "打开原图" }).getAttribute("href")).toBe(url);
  });

  it("shows playable video and its original link even when the task has no findings", async () => {
    vi.spyOn(api, "getAsset").mockResolvedValue({
      ...asset, kind: "video", media_type: "video/mp4", duration_seconds: 9,
    });
    render(<TaskInputPreview task={task} />);
    const player = await screen.findByLabelText("输入视频预览");
    expect(player.getAttribute("src")).toBe(url);
    expect(player.hasAttribute("controls")).toBe(true);
    expect(screen.getByRole("link", { name: "打开原视频" }).getAttribute("href")).toBe(url);
    expect(screen.queryByText("证据帧预览")).toBeNull();
  });

  it("links the first three distinct saved frames by time, excluding the input video and unlocated refs", async () => {
    vi.spyOn(api, "getAsset").mockResolvedValue({
      ...asset, kind: "video", media_type: "video/mp4", duration_seconds: 9,
    });
    const frames = [48, 8, 24, 8, 40].map((index) => ({
      ...evidence, asset: { ...input, asset_id: `frame-${index}` },
      frame_index: index, timestamp_seconds: index / 25,
    }));
    const one = {
      ...finding("video", evidence),
      evidence: [...frames, { ...frames[0], asset: input }, { ...frames[0], frame_index: null, timestamp_seconds: null }],
    };
    render(<TaskInputPreview task={{ ...task, snapshot: { ...task.snapshot, findings: [one] } }} />);
    await screen.findByText("证据帧预览");
    const links = screen.getAllByRole("link", { name: /^帧 / });
    expect(links.map((node) => node.textContent)).toEqual(["帧 8 · 0.32 秒", "帧 24 · 0.96 秒", "帧 40 · 1.6 秒"]);
    expect(links[0].getAttribute("href")).toBe("/api/assets/frame-8/versions/1/content");
    fireEvent.error(links[0].querySelector("img")!);
    expect(screen.getByText("预览不可用")).toBeTruthy();
    expect(links[0].getAttribute("href")).toBe("/api/assets/frame-8/versions/1/content");
  });

  it("only fetches nearby cards and shares an in-flight metadata request for the same asset", async () => {
    const visible: (() => void)[] = [];
    vi.stubGlobal("IntersectionObserver", class {
      constructor(callback: (entries: { isIntersecting: boolean }[]) => void) {
        visible.push(() => callback([{ isIntersecting: true }]));
      }
      observe() {}
      disconnect() {}
    });
    const getAsset = vi.spyOn(api, "getAsset").mockResolvedValue(asset);
    render(<><TaskInputPreview task={task} /><TaskInputPreview task={task} /></>);
    expect(getAsset).not.toHaveBeenCalled();
    await act(async () => { visible.forEach((show) => show()); });
    expect(getAsset).toHaveBeenCalledTimes(1);
    expect(screen.getAllByRole("img")).toHaveLength(2);
  });

  it("ignores late metadata after a source switch and keeps the new original accessible on failure", async () => {
    let resolveOld!: (value: AssetRecord) => void;
    vi.spyOn(api, "getAsset")
      .mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }))
      .mockRejectedValueOnce(new Error("unavailable"));
    const { rerender } = render(<TaskInputPreview task={task} />);
    rerender(<TaskInputPreview task={{ ...task, input_asset: { ...input, version: 2 } }} />);
    await screen.findByText(/预览暂不可用/);
    await act(async () => resolveOld(asset));
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByRole("link", { name: "打开输入文件" }).getAttribute("href")).toBe(url.replace("/1/", "/2/"));
  });
});
