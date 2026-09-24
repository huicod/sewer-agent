import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./test/InspectionWorkbench";
import { ApiError, getComparison } from "./api";
import { ComparisonPanel } from "./ComparisonPanel";
import type { ComparisonResult, ReviewStatus, TaskRecord } from "./types";

const sessionId = "11111111-1111-4111-8111-111111111111";
const requestId = "22222222-2222-4222-8222-222222222222";
const referenceTaskId = "33333333-3333-4333-8333-333333333333";
const currentTaskId = "44444444-4444-4444-8444-444444444444";
const referenceAssetId = "55555555-5555-4555-8555-555555555555";
const currentAssetId = "66666666-6666-4666-8666-666666666666";
const createdAt = "2026-09-13T12:00:00Z";

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } });
}

function finding(taskId: string, index: number, status: ReviewStatus) {
  return {
    finding_id: `${index + 7}7777777-7777-4777-8777-777777777777`,
    task_id: taskId,
    canonical_label: "AJ" as const,
    open_label: null,
    review_status: status,
    source: "detector" as const,
    score: 0.8,
    evidence: [{
      asset: { asset_id: taskId === referenceTaskId ? referenceAssetId : currentAssetId, version: 1 },
      region: null,
      frame_index: 2 + index,
      timestamp_seconds: 1 + index / 2,
    }],
    confirmed_by: status === "confirmed" ? "rules" as const : null,
  };
}

function task(taskId: string, assetId: string, statuses: ReviewStatus[]): TaskRecord {
  return {
    request_id: taskId === referenceTaskId
      ? "88888888-8888-4888-8888-888888888888"
      : "99999999-9999-4999-8999-999999999999",
    session_id: sessionId,
    input_asset: { asset_id: assetId, version: 1 },
    instruction: taskId === referenceTaskId ? "参考检查" : "当前检查",
    created_at: createdAt,
    parent_task_id: null,
    snapshot: {
      schema_version: "1",
      task_id: taskId,
      runtime_status: taskId === currentTaskId ? "partial" : "completed",
      inspection_result: "defects_found",
      stage: "finished",
      stage_progress: 1,
      overall_progress: 1,
      processed_frames: 5,
      processed_chunks: 0,
      active_tool: null,
      can_cancel: false,
      retry_count: 0,
      waiting_reason: taskId === currentTaskId ? "覆盖不足，需补拍" : null,
      updated_at: createdAt,
      findings: statuses.map((status, index) => finding(taskId, index, status)),
      coverage: {
        sufficient_coverage: taskId !== currentTaskId,
        sufficient_quality: true,
        negative_audit_complete: taskId !== currentTaskId,
      },
    },
  };
}

function comparison(): ComparisonResult {
  const reference = task(referenceTaskId, referenceAssetId, ["confirmed"]);
  const current = task(currentTaskId, currentAssetId, ["confirmed", "confirmed", "rejected"]);
  const zero = { confirmed: 0, tentative: 0, needs_review: 0, rejected: 0 };
  return {
    schema_version: "1",
    tool_name: "compare",
    tool_version: "saved-comparison-v1",
    request_id: requestId,
    created_at: createdAt,
    attempt: 2,
    reference,
    current,
    reference_media: {
      ref: reference.input_asset, kind: "video", media_type: "video/mp4", byte_size: 10,
      width: 16, height: 12, duration_seconds: 5, parent: null,
    },
    current_media: {
      ref: current.input_asset, kind: "video", media_type: "video/mp4", byte_size: 10,
      width: 16, height: 12, duration_seconds: 5, parent: null,
    },
    rows: ["AJ", "BX", "FS", "PL", "BLOCK", "OTHER", "UNLABELED"].map((label) => ({
      label: label as ComparisonResult["rows"][number]["label"],
      reference: label === "AJ" ? { ...zero, confirmed: 1 } : { ...zero },
      current: label === "AJ" ? { ...zero, confirmed: 2, rejected: 1 } : { ...zero },
      confirmed_delta: label === "AJ" ? 1 : 0,
    })),
    matching_status: "unverified",
    scope: "saved_findings",
  };
}

beforeEach(() => localStorage.setItem("pipe-inspector.session-id", sessionId));

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("comparison boundary and panel", () => {
  it("strictly validates the snapshot and its request/session binding", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(comparison())));
    await expect(getComparison(sessionId, requestId)).resolves.toEqual(comparison());

    const tampered = structuredClone(comparison()) as ComparisonResult & { checkpoint?: string };
    tampered.checkpoint = "/private/model";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(tampered)));
    await expect(getComparison(sessionId, requestId)).rejects.toEqual(expect.objectContaining<ApiError>({
      name: "ApiError", status: 200, message: "服务返回的数据格式无效",
    }));
  });

  it("fetches on expansion and displays frozen sources, all status buckets, evidence and caveat", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(comparison()));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<ComparisonPanel sessionId={sessionId} requestId={requestId} />);
    expect(fetchMock).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "展开保存结果对比" }));

    expect(await screen.findByText(new RegExp(referenceTaskId))).toBeTruthy();
    expect(screen.getByText(new RegExp(currentTaskId))).toBeTruthy();
    expect(screen.getByText(/覆盖不足，需补拍/)).toBeTruthy();
    expect(screen.getAllByRole("link", { name: /证据 1 · 帧 2 · 1 秒/ })).toHaveLength(2);
    expect(screen.getByText("AJ · 支管暗接")).toBeTruthy();
    expect(screen.getAllByRole("row")).toHaveLength(9);
    expect(screen.getByText(/匹配状态：未经验证/)).toBeTruthy();
    expect(screen.getByText(/不能证明缺陷已修复或出现新缺陷/)).toBeTruthy();
    expect(screen.getByRole<HTMLAnchorElement>("link", { name: "JSON 对比报告" }).href)
      .toContain(`/turns/${requestId}/comparison/report?format=json`);
  });

  it("distinguishes no result from error and ignores a response after closing", async () => {
    let resolve!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise<Response>((done) => { resolve = done; })));
    const user = userEvent.setup();
    const { rerender } = render(<ComparisonPanel sessionId={sessionId} requestId={requestId} />);
    await user.click(screen.getByRole("button", { name: "展开保存结果对比" }));
    await user.click(screen.getByRole("button", { name: "收起保存结果对比" }));
    resolve(response(comparison()));
    await waitFor(() => expect(screen.queryByText("AJ · 支管暗接")).toBeNull());

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(null)));
    rerender(<ComparisonPanel sessionId={sessionId} requestId="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" />);
    await user.click(screen.getByRole("button", { name: "展开保存结果对比" }));
    expect(await screen.findByText("该对话没有已保存的对比结果。")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("renders an API failure instead of the successful no-result message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({
      detail: { code: "storage_unavailable", message: "对比证据暂不可用" },
    }, 503)));
    const user = userEvent.setup();
    render(<ComparisonPanel sessionId={sessionId} requestId={requestId} />);
    await user.click(screen.getByRole("button", { name: "展开保存结果对比" }));

    expect((await screen.findByRole("alert")).textContent).toContain("对比证据暂不可用");
    expect(screen.queryByText("该对话没有已保存的对比结果。")).toBeNull();
  });

  it("offers a completed text-derived comparison even when the turn body reference is null", async () => {
    const snapshot = comparison();
    const textOnlyTurn = {
      request_id: requestId, session_id: sessionId, text: `与 ${referenceTaskId} 比较`, input_asset: null,
      task_id: null, reference_task_id: null, created_at: createdAt, updated_at: createdAt,
      status: "completed", stage: "finished", reply: "已完成保存结果对比",
      linked_task_id: currentTaskId, error_code: null, attempts: 1,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({
        session: { session_id: sessionId, created_at: createdAt },
        tasks: [snapshot.reference, snapshot.current],
      }))
      .mockResolvedValueOnce(response([textOnlyTurn]))
      .mockResolvedValueOnce(response(snapshot));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<App pollIntervalMs={60_000} />);
    await screen.findByText("已完成保存结果对比");
    await user.click(screen.getByRole("button", { name: "展开保存结果对比" }));

    expect(await screen.findByText("AJ · 支管暗接")).toBeTruthy();
    expect(fetchMock).toHaveBeenLastCalledWith(
      `/api/sessions/${sessionId}/turns/${requestId}/comparison`,
      undefined,
    );
  });
});

describe("comparison composer identity", () => {
  it("freezes the explicit reference across retry and never uploads a comparison attachment", async () => {
    const reference = comparison().reference;
    const current = comparison().current;
    const completedTurn = {
      request_id: requestId, session_id: sessionId, text: "比较这两个保存结果", input_asset: null,
      task_id: currentTaskId, reference_task_id: referenceTaskId, created_at: createdAt,
      updated_at: createdAt, status: "queued", stage: "queued", reply: null,
      linked_task_id: null, error_code: null, attempts: 0,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response({ session: { session_id: sessionId, created_at: createdAt }, tasks: [reference, current] }))
      .mockResolvedValueOnce(response([]))
      .mockRejectedValueOnce(new TypeError("connection reset"))
      .mockResolvedValueOnce(response(completedTurn, 202));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const user = userEvent.setup();
    render(<App pollIntervalMs={60_000} />);
    await screen.findByText(sessionId);
    await user.selectOptions(screen.getByLabelText("关联已有任务（可选）"), currentTaskId);
    await user.selectOptions(screen.getByLabelText("对比参考任务（可选）"), referenceTaskId);
    await user.type(screen.getByLabelText("对话内容"), "比较这两个保存结果");
    await user.click(screen.getByRole("button", { name: "发送消息" }));
    expect((await screen.findByRole("alert")).textContent).toContain("相同请求编号");
    await user.click(screen.getByRole("button", { name: "重新发送" }));

    const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(posts).toHaveLength(2);
    expect((posts[0][1] as RequestInit).body).toBe((posts[1][1] as RequestInit).body);
    expect(JSON.parse(String((posts[0][1] as RequestInit).body))).toEqual({
      request_id: requestId, text: "比较这两个保存结果", input_asset: null,
      task_id: currentTaskId, reference_task_id: referenceTaskId, inspection_mode: "yolo_vlm",
    });
    expect(fetchMock.mock.calls.some(([url]) => url === "/api/assets")).toBe(false);
  });
});
