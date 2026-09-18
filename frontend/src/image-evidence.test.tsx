import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { ApiError, getImageEvidence } from "./api";
import { ImageEvidencePanel } from "./ImageEvidencePanel";
import type { ImageEvidenceDetail, TaskRecord } from "./types";

const sessionId = "33333333-3333-4333-8333-333333333333";
const taskId = "44444444-4444-4444-8444-444444444444";
const otherTaskId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const parentTaskId = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const assetId = "11111111-1111-4111-8111-111111111111";
const findingId = "55555555-5555-4555-8555-555555555555";
const additionFindingId = "66666666-6666-4666-8666-666666666666";
const rejectedFindingId = "77777777-7777-4777-8777-777777777777";
const createdAt = "2026-09-16T12:00:00Z";

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function detail(id = taskId): ImageEvidenceDetail {
  const input = { asset_id: assetId, version: 1 };
  return {
    task_id: id,
    input_asset: input,
    parent_task_id: null,
    observation: {
      detection: {
        detector_id: "yolov26-sewer5",
        model_version: "best-v1",
        label_space_version: "sewer5-v1",
        width: 640,
        height: 480,
        detections: [{
          label: "FS",
          native_label: "FS",
          score: 0.8038,
          bbox_xyxy: [64, 48, 320, 240],
        }],
      },
      quality: {
        mean_luminance: 91,
        contrast: 27,
        sharpness: 18,
        adequate: true,
        reasons: [],
      },
    },
    review: {
      image_assessable: true,
      full_image_audited: true,
      candidates: [{
        index: 0,
        verdict: "revise",
        proposed_label: "PL",
        reason: "区域呈连续裂纹形态，不像腐蚀表面。",
        evidence_strength: "clear",
      }],
      additional_anomalies: [{
        label: "BLOCK",
        name: null,
        bbox_xyxy: [400, 260, 520, 390],
        reason: "右下可见独立沉积区域。",
        evidence_strength: "uncertain",
      }],
      summary: "原候选建议改类，并发现一处待人工确认的新增异常。",
    },
    fusion_changes: [{
      index: 0,
      additional_index: null,
      action: "revise",
      pending: false,
      label: "PL",
      bbox_xyxy: [64, 48, 320, 240],
      reason: "区域呈连续裂纹形态，不像腐蚀表面。",
      vlm_verdict: "revise",
      proposed_label: "PL",
      gate: null,
    }, {
      index: null,
      additional_index: 0,
      action: "add_pending",
      pending: true,
      label: "BLOCK",
      bbox_xyxy: [400, 260, 520, 390],
      reason: "右下可见独立沉积区域。",
      vlm_verdict: null,
      proposed_label: null,
      gate: "缺少明确且可定位的证据",
    }],
    findings: [{
      finding_id: findingId,
      task_id: id,
      canonical_label: "PL",
      open_label: null,
      review_status: "confirmed",
      source: "human",
      score: 0.8038,
      evidence: [{
        asset: input,
        region: { width: 640, height: 480, bbox_xyxy: [64, 48, 320, 240] },
        frame_index: null,
        timestamp_seconds: null,
      }],
      confirmed_by: "human",
    }, {
      finding_id: additionFindingId,
      task_id: id,
      canonical_label: "BLOCK",
      open_label: null,
      review_status: "needs_review",
      source: "vlm",
      score: null,
      evidence: [{
        asset: input,
        region: { width: 640, height: 480, bbox_xyxy: [400, 260, 520, 390] },
        frame_index: null,
        timestamp_seconds: null,
      }],
      confirmed_by: null,
    }, {
      finding_id: rejectedFindingId,
      task_id: id,
      canonical_label: "FS",
      open_label: null,
      review_status: "rejected",
      source: "human",
      score: 0.8038,
      evidence: [{
        asset: input,
        region: { width: 640, height: 480, bbox_xyxy: [100, 100, 180, 180] },
        frame_index: null,
        timestamp_seconds: null,
      }],
      confirmed_by: null,
    }],
  };
}

function task(): TaskRecord {
  return {
    request_id: "22222222-2222-4222-8222-222222222222",
    session_id: sessionId,
    input_asset: { asset_id: assetId, version: 1 },
    instruction: "检查图片",
    created_at: createdAt,
    snapshot: {
      schema_version: "1",
      task_id: taskId,
      runtime_status: "completed",
      inspection_result: "defects_found",
      stage: "finished",
      stage_progress: 1,
      overall_progress: 1,
      processed_frames: 1,
      processed_chunks: 1,
      active_tool: null,
      retry_count: 0,
      waiting_reason: null,
      updated_at: createdAt,
      findings: detail().findings,
      coverage: {
        sufficient_coverage: true,
        sufficient_quality: true,
        negative_audit_complete: true,
      },
    },
  };
}

beforeEach(() => {
  localStorage.setItem("pipe-inspector.session-id", sessionId);
});

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("image evidence HTTP boundary", () => {
  it("accepts the complete same-task stored provenance response", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(detail()));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getImageEvidence(taskId)).resolves.toEqual(detail());
    expect(fetchMock).toHaveBeenCalledWith(`/api/tasks/${taskId}/image-evidence`, undefined);
  });

  it("accepts a legacy review with nullable evidence strength", async () => {
    const payload = detail();
    payload.review!.candidates[0].evidence_strength = null;
    payload.review!.additional_anomalies[0].evidence_strength = null;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(payload)));

    await expect(getImageEvidence(taskId)).resolves.toEqual(payload);
  });

  it.each([
    ["wrong task", (payload: ImageEvidenceDetail) => { payload.task_id = otherTaskId; }],
    ["out-of-bounds detector box", (payload: ImageEvidenceDetail) => {
      payload.observation!.detection.detections[0].bbox_xyxy = [64, 48, 700, 240];
    }],
    ["synthetic VLM addition score", (payload: ImageEvidenceDetail) => {
      (payload.review!.additional_anomalies[0] as unknown as Record<string, unknown>).score = 1;
    }],
    ["unknown evidence strength", (payload: ImageEvidenceDetail) => {
      (payload.review!.candidates[0] as unknown as Record<string, unknown>).evidence_strength = "ambiguous";
    }],
  ])("rejects %s", async (_name, mutate) => {
    const payload = structuredClone(detail());
    mutate(payload);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(payload)));

    await expect(getImageEvidence(taskId)).rejects.toEqual(expect.objectContaining<ApiError>({
      name: "ApiError",
      status: 200,
      message: "服务返回的数据格式无效",
    }));
  });
});

describe("stored image inspection provenance panel", () => {
  it("loads and displays coexisting labels without replacing the original class", async () => {
    const payload = detail();
    Object.assign(payload.review!.candidates[0], { coexisting_labels: ["PL"] });
    Object.assign(payload.fusion_changes[0], { coexisting_labels: ["PL"] });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(payload)));
    const user = userEvent.setup();
    render(<ImageEvidencePanel taskId={taskId} snapshotRevision={createdAt} />);
    await user.click(screen.getByRole("button", { name: "展开检查依据" }));
    expect(await screen.findAllByText(/共存建议 PL/)).toHaveLength(2);
    expect(screen.getByText(/候选 #0 · FS · 原始检测器分数 0.8038/)).toBeTruthy();
  });

  it("separates the original FS score, indexed VLM revision/addition, fusion, and current findings", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(detail()));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<ImageEvidencePanel taskId={taskId} snapshotRevision={createdAt} />);
    expect(fetchMock).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "展开检查依据" }));

    const detector = await screen.findByRole("img", { name: "原始检测候选叠加图" });
    expect(detector.getAttribute("viewBox")).toBe("0 0 640 480");
    expect(detector.querySelector("image")?.getAttribute("href")).toBe(
      `/api/assets/${assetId}/versions/1/content`,
    );
    const detectorBox = detector.querySelector("rect");
    expect([
      detectorBox?.getAttribute("x"),
      detectorBox?.getAttribute("y"),
      detectorBox?.getAttribute("width"),
      detectorBox?.getAttribute("height"),
    ]).toEqual(["64", "48", "256", "192"]);
    expect(detector.textContent).toContain("#0 FS");
    expect(screen.getByText(/候选 #0 · FS · 原始检测器分数 0.8038/)).toBeTruthy();
    expect(screen.getByText(/VLM 候选 #0.*建议 PL/)).toBeTruthy();

    const additions = screen.getByRole("region", { name: "VLM 新增建议" });
    expect(within(additions).getByText(/新增 #0 · BLOCK/)).toBeTruthy();
    expect(within(additions).getByText(/右下可见独立沉积区域/)).toBeTruthy();
    expect(within(additions).queryByText(/分数|置信度/)).toBeNull();

    expect(screen.getByText(/候选 #0 · revise · 输出标签 PL · 已自动处理/)).toBeTruthy();
    expect(screen.getByText(/新增 #0 · add_pending · 输出标签 BLOCK · 待人工处理/)).toBeTruthy();

    const effective = screen.getByRole("img", { name: "当前有效发现叠加图" });
    expect(effective.querySelectorAll("rect")).toHaveLength(2);
    expect(effective.textContent).toContain(`PL 已确认 ${findingId.slice(0, 8)}`);
    expect(effective.textContent).toContain(`BLOCK 需复核 ${additionFindingId.slice(0, 8)}`);
    expect(screen.getByText(new RegExp(`${findingId}.*PL.*已确认`))).toBeTruthy();
    expect(screen.getByText(new RegExp(`${rejectedFindingId}.*FS.*已排除`))).toBeTruthy();
    expect(screen.getByText(/已排除项不绘制为当前有效缺陷/)).toBeTruthy();
    expect(screen.queryByText(/PL.*原始检测器分数 0.8038/)).toBeNull();
  });

  it("states that missing review and legacy fusion history are unavailable and links the parent", async () => {
    const payload = detail();
    payload.parent_task_id = parentTaskId;
    payload.review = null;
    payload.fusion_changes = [];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(payload)));
    const user = userEvent.setup();

    render(<ImageEvidencePanel taskId={taskId} snapshotRevision={createdAt} />);
    await user.click(screen.getByRole("button", { name: "展开检查依据" }));

    expect(await screen.findByText(/本任务没有已保存的独立 VLM 复核/)).toBeTruthy();
    expect(screen.getByText(/历史融合动作未保存/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "查看原任务报告" }).getAttribute("href"))
      .toBe(`/api/tasks/${parentTaskId}/report?format=html`);
    expect(screen.queryByText(/复用原任务 VLM 复核|重新复核/)).toBeNull();
  });

  it("shows a stored-empty task without presenting review or findings as successful", async () => {
    const payload = detail();
    payload.observation = null;
    payload.review = null;
    payload.fusion_changes = [];
    payload.findings = [];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(payload)));
    const user = userEvent.setup();

    render(<ImageEvidencePanel taskId={taskId} snapshotRevision={createdAt} />);
    await user.click(screen.getByRole("button", { name: "展开检查依据" }));

    expect(await screen.findByText("本任务没有已保存的原始检测观察。")).toBeTruthy();
    expect(screen.getByText(/本任务没有已保存的独立 VLM 复核/)).toBeTruthy();
    expect(screen.getByText("当前任务快照没有发现项。")).toBeTruthy();
    expect(screen.queryByRole("img", { name: "当前有效发现叠加图" })).toBeNull();
  });

  it("discards a stale response after switching tasks and refreshes an expanded revision", async () => {
    let resolveFirst!: (value: Response) => void;
    const first = new Promise<Response>((resolve) => { resolveFirst = resolve; });
    const fetchMock = vi.fn()
      .mockReturnValueOnce(first)
      .mockResolvedValueOnce(response(detail(otherTaskId)))
      .mockResolvedValueOnce(response(detail(otherTaskId)));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const { rerender } = render(<ImageEvidencePanel taskId={taskId} snapshotRevision="r1" />);

    await user.click(screen.getByRole("button", { name: "展开检查依据" }));
    rerender(<ImageEvidencePanel taskId={otherTaskId} snapshotRevision="r2" />);
    resolveFirst(response(detail()));
    await waitFor(() => expect(screen.queryByText(/原始检测器分数/)).toBeNull());

    await user.click(screen.getByRole("button", { name: "展开检查依据" }));
    expect(await screen.findByText(/原始检测器分数 0.8038/)).toBeTruthy();
    rerender(<ImageEvidencePanel taskId={otherTaskId} snapshotRevision="r3" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
  });
});

describe("TaskCard image evidence integration", () => {
  it("offers provenance only after an image is identified", async () => {
    const imageTask = task();
    const fetchMock = vi.fn(async (url: string) => {
      if (url === `/api/sessions/${sessionId}`) {
        return response({ session: { session_id: sessionId, created_at: createdAt }, tasks: [imageTask] });
      }
      if (url === `/api/sessions/${sessionId}/turns`) return response([]);
      if (url === `/api/assets/${assetId}/versions/1`) {
        return response({
          ref: imageTask.input_asset,
          kind: "image",
          media_type: "image/jpeg",
          byte_size: 1024,
          width: 640,
          height: 480,
          duration_seconds: null,
          parent: null,
        });
      }
      if (url === `/api/tasks/${taskId}/image-evidence`) return response(detail());
      throw new Error(`unexpected URL ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<App pollIntervalMs={60_000} />);
    expect(await screen.findByRole("button", { name: "查看媒体证据" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "展开检查依据" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "查看媒体证据" }));
    await user.click(await screen.findByRole("button", { name: "展开检查依据" }));
    expect(await screen.findByRole("img", { name: "原始检测候选叠加图" })).toBeTruthy();
  });
});
