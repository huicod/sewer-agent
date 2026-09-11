import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReviewPanel } from "./ReviewPanel";
import { VideoTimeline } from "./VideoTimeline";
import type { HumanReviewDetail, HumanReviewRecord, TaskSnapshot, VideoDetail } from "./types";

const taskId = "44444444-4444-4444-8444-444444444444";
const firstFindingId = "55555555-5555-4555-8555-555555555555";
const secondFindingId = "66666666-6666-4666-8666-666666666666";
const sourceAssetId = "11111111-1111-4111-8111-111111111111";
const frameAssetId = "77777777-7777-4777-8777-777777777777";
const updatedAt = "2026-09-11T12:00:00Z";

function snapshot(runtimeStatus: TaskSnapshot["runtime_status"] = "waiting_review"): TaskSnapshot {
  return {
    schema_version: "1",
    task_id: taskId,
    runtime_status: runtimeStatus,
    inspection_result: "inconclusive",
    stage: runtimeStatus === "waiting_review" ? "human_review" : "finished",
    stage_progress: runtimeStatus === "waiting_review" ? 0 : 1,
    overall_progress: runtimeStatus === "waiting_review" ? 0.95 : 1,
    processed_frames: 12,
    processed_chunks: 1,
    can_cancel: runtimeStatus === "waiting_review",
    active_tool: null,
    retry_count: 0,
    waiting_reason: runtimeStatus === "waiting_review" ? "需要人工确认" : null,
    updated_at: updatedAt,
    findings: [
      {
        finding_id: firstFindingId,
        task_id: taskId,
        canonical_label: "BX",
        open_label: null,
        review_status: "needs_review",
        source: "detector",
        score: 0.72,
        confirmed_by: null,
        evidence: [{
          asset: { asset_id: sourceAssetId, version: 1 },
          region: { width: 640, height: 480, bbox_xyxy: [64, 48, 320, 240] },
          frame_index: null,
          timestamp_seconds: null,
        }],
      },
      {
        finding_id: secondFindingId,
        task_id: taskId,
        canonical_label: "FS",
        open_label: null,
        review_status: "confirmed",
        source: "vlm",
        score: null,
        confirmed_by: "rules",
        evidence: [{
          asset: { asset_id: sourceAssetId, version: 1 },
          region: null,
          frame_index: 12,
          timestamp_seconds: 4.25,
        }],
      },
    ],
    coverage: {
      sufficient_coverage: true,
      sufficient_quality: true,
      negative_audit_complete: false,
    },
  };
}

function reviewDetail(): HumanReviewDetail {
  return { task_id: taskId, can_review: true, snapshot: snapshot(), record: null };
}

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("human review panel", () => {
  it("does not auto-select pending findings and blocks an incomplete complete-batch review", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(reviewDetail())));
    const user = userEvent.setup();

    render(<ReviewPanel taskId={taskId} snapshotRevision={updatedAt} runtimeStatus="waiting_review" sourceAsset={{ asset_id: sourceAssetId, version: 1 }} onAccepted={() => undefined} />);

    await screen.findByRole("heading", { name: "人工复核" });
    expect(screen.queryAllByRole("radio", { checked: true })).toHaveLength(0);
    expect(screen.getByRole("button", { name: "提交完整复核" }).hasAttribute("disabled")).toBe(true);

    const first = screen.getByTestId(`review-finding-${firstFindingId}`);
    await user.click(within(first).getByRole("radio", { name: "确认" }));
    await user.type(within(first).getByLabelText("处理理由"), "画面中可见结构破损");

    expect(screen.getByRole("button", { name: "提交完整复核" }).hasAttribute("disabled")).toBe(true);
  });

  it("draws the evidence in original coordinates and keeps the real timestamp", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(reviewDetail())));

    render(<ReviewPanel taskId={taskId} snapshotRevision={updatedAt} runtimeStatus="waiting_review" sourceAsset={{ asset_id: sourceAssetId, version: 1 }} onAccepted={() => undefined} />);

    const evidence = await screen.findByLabelText("BX 原始证据 1");
    expect(evidence.getAttribute("viewBox")).toBe("0 0 640 480");
    const image = evidence.querySelector("image");
    expect(image?.getAttribute("href")).toBe(
      `/api/assets/${sourceAssetId}/versions/1/content`,
    );
    const box = evidence.querySelector("rect");
    expect([box?.getAttribute("x"), box?.getAttribute("y"), box?.getAttribute("width"), box?.getAttribute("height")])
      .toEqual(["64", "48", "256", "192"]);
    expect(screen.getByText((_, node) => node?.tagName === "FIGCAPTION" && node.textContent === "4.25 秒 · 帧 12")).toBeTruthy();
  });

  it("posts confirm, OTHER relabel and audit fields as one exact snapshot submission", async () => {
    const after = snapshot("completed");
    const record: HumanReviewRecord = {
      task_id: taskId,
      submission: {
        request_id: "88888888-8888-4888-8888-888888888888",
        reviewer: "huicod",
        snapshot_updated_at: updatedAt,
        decisions: [
          { finding_id: firstFindingId, action: "confirm", label: null, open_label: null, reason: "证据清楚" },
          { finding_id: secondFindingId, action: "relabel", label: "OTHER", open_label: "沉积物", reason: "原标签不符" },
        ],
        negative_audit_complete: true,
        note: "已检查整张图",
      },
      before: snapshot(),
      after,
      reviewed_at: "2026-09-11T12:01:00Z",
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(reviewDetail()))
      .mockResolvedValueOnce(response(record));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(
      "88888888-8888-4888-8888-888888888888",
    );
    const accepted = vi.fn();
    const user = userEvent.setup();

    render(<ReviewPanel taskId={taskId} snapshotRevision={updatedAt} runtimeStatus="waiting_review" sourceAsset={{ asset_id: sourceAssetId, version: 1 }} onAccepted={accepted} />);
    await screen.findByRole("heading", { name: "人工复核" });
    const first = screen.getByTestId(`review-finding-${firstFindingId}`);
    const second = screen.getByTestId(`review-finding-${secondFindingId}`);
    await user.click(within(first).getByRole("radio", { name: "确认" }));
    await user.type(within(first).getByLabelText("处理理由"), "证据清楚");
    await user.click(within(second).getByRole("radio", { name: "改标" }));
    await user.selectOptions(within(second).getByLabelText("新标签"), "OTHER");
    await user.type(within(second).getByLabelText("OTHER 名称"), "沉积物");
    await user.type(within(second).getByLabelText("处理理由"), "原标签不符");
    await user.click(screen.getByLabelText("已完成人工阴性审计"));
    await user.type(screen.getByLabelText("审核备注（可选）"), "已检查整张图");
    await user.click(screen.getByRole("button", { name: "提交完整复核" }));

    await waitFor(() => expect(accepted).toHaveBeenCalledWith(after));
    const [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url).toBe(`/api/tasks/${taskId}/review`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual(record.submission);
    expect(await screen.findByText("审核记录")).toBeTruthy();
    expect(screen.getByText("已检查整张图")).toBeTruthy();
  });

  it("reuses a failed identical request id, then creates a new id after an edit", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({
        ...reviewDetail(),
        snapshot: { ...snapshot(), findings: [snapshot().findings[0]] },
      }))
      .mockResolvedValueOnce(response({ detail: { message: "暂时无法提交" } }, 503))
      .mockResolvedValueOnce(response({ detail: { message: "仍然无法提交" } }, 503))
      .mockResolvedValueOnce(response({ detail: { message: "修改后仍失败" } }, 503));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(globalThis.crypto, "randomUUID")
      .mockReturnValueOnce("88888888-8888-4888-8888-888888888888")
      .mockReturnValueOnce("99999999-9999-4999-8999-999999999999");
    const user = userEvent.setup();

    render(<ReviewPanel taskId={taskId} snapshotRevision={updatedAt} runtimeStatus="waiting_review" sourceAsset={{ asset_id: sourceAssetId, version: 1 }} onAccepted={() => undefined} />);
    const finding = await screen.findByTestId(`review-finding-${firstFindingId}`);
    await user.click(within(finding).getByRole("radio", { name: "拒绝" }));
    const reason = within(finding).getByLabelText("处理理由");
    await user.type(reason, "并非缺陷");
    await user.click(screen.getByRole("button", { name: "提交完整复核" }));
    await screen.findByText("暂时无法提交");
    await user.click(screen.getByRole("button", { name: "重新提交完整复核" }));
    await screen.findByText("仍然无法提交");
    await user.type(reason, "，是反光");
    await user.click(screen.getByRole("button", { name: "提交完整复核" }));
    await screen.findByText("修改后仍失败");

    const ids = fetchMock.mock.calls.slice(1).map(([, init]) =>
      JSON.parse(String((init as RequestInit).body)).request_id,
    );
    expect(ids).toEqual([
      "88888888-8888-4888-8888-888888888888",
      "88888888-8888-4888-8888-888888888888",
      "99999999-9999-4999-8999-999999999999",
    ]);
  });

  it("keeps the original input reachable for a zero-finding negative audit", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      ...reviewDetail(),
      snapshot: { ...snapshot(), findings: [] },
    })));

    render(<ReviewPanel taskId={taskId} snapshotRevision={updatedAt} runtimeStatus="waiting_review" sourceAsset={{ asset_id: sourceAssetId, version: 1 }} onAccepted={() => undefined} />);

    const source = await screen.findByRole("link", { name: "打开原始输入" });
    expect(source.getAttribute("href")).toBe(`/api/assets/${sourceAssetId}/versions/1/content`);
  });

  it("does not offer confirmation for an unlabeled finding that the server requires to be relabeled", async () => {
    const unknown = snapshot();
    unknown.findings = [{ ...unknown.findings[0], canonical_label: null }];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      ...reviewDetail(), snapshot: unknown,
    })));

    render(<ReviewPanel taskId={taskId} snapshotRevision={updatedAt} runtimeStatus="waiting_review" sourceAsset={{ asset_id: sourceAssetId, version: 1 }} onAccepted={() => undefined} />);

    const finding = await screen.findByTestId(`review-finding-${firstFindingId}`);
    expect(within(finding).getByRole("radio", { name: "确认" }).hasAttribute("disabled")).toBe(true);
    expect(within(finding).getByRole("radio", { name: "改标" }).hasAttribute("disabled")).toBe(false);
  });

  it("preserves a draft for an unchanged poll but reloads when the task snapshot changes", async () => {
    const terminal = snapshot("completed");
    terminal.updated_at = "2026-09-11T12:02:00Z";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(reviewDetail()))
      .mockResolvedValueOnce(response({ task_id: taskId, can_review: false, snapshot: terminal, record: null }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const props = {
      taskId,
      snapshotRevision: updatedAt,
      runtimeStatus: "waiting_review" as const,
      sourceAsset: { asset_id: sourceAssetId, version: 1 },
      onAccepted: () => undefined,
    };
    const { rerender } = render(<ReviewPanel {...props} />);
    const finding = await screen.findByTestId(`review-finding-${firstFindingId}`);
    await user.type(within(finding).getByLabelText("处理理由"), "保留中的草稿");

    rerender(<ReviewPanel {...props} />);
    expect((within(finding).getByLabelText("处理理由") as HTMLTextAreaElement).value).toBe("保留中的草稿");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    rerender(<ReviewPanel {...props} snapshotRevision={terminal.updated_at} runtimeStatus="completed" />);
    expect(await screen.findByText("该历史任务没有可用的结构化人工审核记录。")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.queryByDisplayValue("保留中的草稿")).toBeNull();
  });
});

describe("persisted video timeline", () => {
  it("shows sorted real intervals and links evidence frames by frame index", async () => {
    const detail: VideoDetail = {
      task_id: taskId,
      scan: {
        state: {
          source: { asset_id: sourceAssetId, version: 1 },
          config: {
            policy_version: "video-events-v1",
            iou_threshold: 0.3,
            max_gap_frames: 3,
            max_gap_seconds: 1,
            evidence_separation_seconds: 0.25,
            audit_interval_seconds: 10,
          },
          detector_id: "yolo26",
          model_version: "five-class-v1",
          label_space_version: "sewer-five-v1",
          next_frame: 40,
          last_timestamp_seconds: 8.5,
          adequate_frames: 38,
          active: [],
          events: [
            {
              event_id: "later",
              label: "FS",
              start_frame: 20,
              end_frame: 24,
              start_seconds: 5.5,
              end_seconds: 6.75,
              observations: 3,
              last_region: { width: 640, height: 480, bbox_xyxy: [20, 30, 80, 100] },
              evidence: [{ frame_index: 24, timestamp_seconds: 6.75, width: 640, height: 480, quality: { mean_luminance: 112, contrast: 31, sharpness: 84, adequate: true, reasons: [] }, detection_index: 0, region: { width: 640, height: 480, bbox_xyxy: [20, 30, 80, 100] }, score: 0.8 }],
            },
            {
              event_id: "earlier",
              label: "BX",
              start_frame: 10,
              end_frame: 12,
              start_seconds: 2.25,
              end_seconds: 4.25,
              observations: 2,
              last_region: { width: 640, height: 480, bbox_xyxy: [64, 48, 320, 240] },
              evidence: [{ frame_index: 12, timestamp_seconds: 4.25, width: 640, height: 480, quality: { mean_luminance: 112, contrast: 31, sharpness: 84, adequate: true, reasons: [] }, detection_index: 0, region: { width: 640, height: 480, bbox_xyxy: [64, 48, 320, 240] }, score: 0.72 }],
            },
          ],
          audit_frames: [],
          finalized: true,
        },
        decode_complete: false,
        error_code: "decode_interrupted",
        inspection_result: "pending",
      },
      evidence: {
        frames: [{
          frame_index: 12,
          timestamp_seconds: 4.25,
          asset: { asset_id: frameAssetId, version: 1 },
        }],
        review_frames: [12],
      },
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(detail)));
    const user = userEvent.setup();

    render(<VideoTimeline taskId={taskId} snapshotRevision={updatedAt} />);
    expect(screen.queryByText("视频候选时间线")).toBeNull();
    await user.click(screen.getByRole("button", { name: "展开视频候选时间线" }));

    await screen.findByText("视频候选时间线");
    const events = screen.getAllByRole("listitem");
    expect(events[0].textContent).toContain("2.25–4.25 秒");
    expect(events[0].textContent).toContain("帧 10–12");
    expect(events[1].textContent).toContain("5.5–6.75 秒");
    expect(screen.getByText("解码未完整：decode_interrupted")).toBeTruthy();
    const frameLink = within(events[0]).getByRole("link", { name: "证据帧 12 · 4.25 秒" });
    expect(frameLink.getAttribute("href")).toBe(`/api/assets/${frameAssetId}/versions/1/content`);
    expect(frameLink.querySelector("img")?.getAttribute("src")).toBe(
      `/api/assets/${frameAssetId}/versions/1/content`,
    );
    expect(frameLink.querySelector("img")?.getAttribute("loading")).toBe("lazy");
    expect(within(events[1]).queryByRole("link")).toBeNull();
  });

  it("clears loaded evidence when the component switches to another task", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ task_id: taskId, scan: null, evidence: null })));
    const user = userEvent.setup();
    const { rerender } = render(<VideoTimeline taskId={taskId} snapshotRevision={updatedAt} />);
    await user.click(screen.getByRole("button", { name: "展开视频候选时间线" }));
    await screen.findByText("尚无持久化视频扫描结果。");

    rerender(<VideoTimeline taskId="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" snapshotRevision={updatedAt} />);

    expect(screen.queryByText("尚无持久化视频扫描结果。")).toBeNull();
    expect(screen.queryByText("视频候选时间线")).toBeNull();
  });

  it("refreshes an expanded null scan on a new snapshot revision without collapsing", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ task_id: taskId, scan: null, evidence: null }))
      .mockResolvedValueOnce(response({ task_id: taskId, scan: null, evidence: null }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const { rerender } = render(<VideoTimeline taskId={taskId} snapshotRevision={updatedAt} />);
    await user.click(screen.getByRole("button", { name: "展开视频候选时间线" }));
    await screen.findByText("尚无持久化视频扫描结果。");

    rerender(<VideoTimeline taskId={taskId} snapshotRevision="2026-09-11T12:03:00Z" />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "展开视频候选时间线" }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("视频候选时间线")).toBeTruthy();
  });

  it("does not load timeline data for revisions received while collapsed", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ task_id: taskId, scan: null, evidence: null }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const { rerender } = render(<VideoTimeline taskId={taskId} snapshotRevision={updatedAt} />);

    rerender(<VideoTimeline taskId={taskId} snapshotRevision="2026-09-11T12:03:00Z" />);
    expect(fetchMock).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "展开视频候选时间线" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  });
});
