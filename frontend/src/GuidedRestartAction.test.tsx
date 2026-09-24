import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GuidedRestartAction } from "./GuidedRestartAction";
import type { TaskRecord } from "./types";

const sourceTaskId = "44444444-4444-4444-8444-444444444444";
const admittedTaskId = "55555555-5555-4555-8555-555555555555";
const requestId = "66666666-6666-4666-8666-666666666666";
const createdAt = "2026-09-24T09:00:00Z";

function task(changes: Partial<TaskRecord> = {}): TaskRecord {
  return {
    request_id: "22222222-2222-4222-8222-222222222222",
    session_id: "33333333-3333-4333-8333-333333333333",
    input_asset: { asset_id: "11111111-1111-4111-8111-111111111111", version: 1 },
    instruction: "原纯 YOLO 检查",
    inspection_mode: "yolo_only",
    created_at: createdAt,
    parent_task_id: null,
    frame_source: null,
    snapshot: {
      schema_version: "1",
      task_id: sourceTaskId,
      runtime_status: "completed",
      inspection_result: "inconclusive",
      stage: "finished",
      stage_progress: 1,
      overall_progress: 1,
      processed_frames: 1,
      processed_chunks: 0,
      active_tool: null,
      retry_count: 0,
      waiting_reason: null,
      updated_at: createdAt,
      findings: [],
      coverage: {
        sufficient_coverage: true,
        sufficient_quality: true,
        negative_audit_complete: false,
      },
    },
    ...changes,
  };
}

function response(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("GuidedRestartAction", () => {
  it("only offers recovery for a root pure-YOLO image task", () => {
    const props = {
      disabled: false,
      onAdmitted: vi.fn(),
      onStarted: vi.fn(),
      onBusyChange: vi.fn(),
    };
    const { rerender } = render(<GuidedRestartAction task={task()} {...props} />);
    expect(screen.getByRole("button", { name: "用原图新建复检" })).toBeTruthy();
    expect(screen.getByText(/同一原图新建 YOLO＋VLM 任务/)).toBeTruthy();

    rerender(<GuidedRestartAction task={task({ inspection_mode: "yolo_vlm" })} {...props} />);
    expect(screen.queryByRole("button")).toBeNull();
    rerender(<GuidedRestartAction task={task({ parent_task_id: admittedTaskId })} {...props} />);
    expect(screen.queryByRole("button")).toBeNull();
    rerender(<GuidedRestartAction task={task({ frame_source: {
      video_asset: { asset_id: "77777777-7777-4777-8777-777777777777", version: 1 },
      frame_index: 3,
      timestamp_seconds: 1.5,
    } })} {...props} />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("creates and starts an independent VLM baseline from the exact original asset", async () => {
    const admitted = task({
      request_id: requestId,
      inspection_mode: "yolo_vlm",
      instruction: "使用同一原图执行五类缺陷检测和 VLM 基线复检。",
      snapshot: { ...task().snapshot, task_id: admittedTaskId, runtime_status: "queued" },
    });
    const started = { ...admitted, snapshot: { ...admitted.snapshot, runtime_status: "running" as const } };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(admitted, 201))
      .mockResolvedValueOnce(response(started, 202));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const onAdmitted = vi.fn();
    const onStarted = vi.fn();
    const onBusyChange = vi.fn();

    await userEvent.click(render(
      <GuidedRestartAction task={task()} disabled={false} onAdmitted={onAdmitted}
        onStarted={onStarted} onBusyChange={onBusyChange} />,
    ).getByRole("button", { name: "用原图新建复检" }));

    await waitFor(() => expect(onStarted).toHaveBeenCalledWith(started));
    const create = fetchMock.mock.calls[0];
    expect(create?.[0]).toBe("/api/tasks");
    expect(JSON.parse(String((create?.[1] as RequestInit).body))).toEqual({
      request_id: requestId,
      session_id: "33333333-3333-4333-8333-333333333333",
      input_asset: { asset_id: "11111111-1111-4111-8111-111111111111", version: 1 },
      instruction: "使用同一原图执行五类缺陷检测和 VLM 基线复检。",
      inspection_mode: "yolo_vlm",
    });
    expect(fetchMock.mock.calls[1]?.[0]).toBe(`/api/tasks/${admittedTaskId}/start`);
    expect(onAdmitted).toHaveBeenCalledWith(admitted);
    expect(onBusyChange.mock.calls.map(([busy]) => busy)).toEqual([true, false]);
  });

  it("retries an uncertain admission with the identical request payload", async () => {
    const admitted = task({
      request_id: requestId,
      inspection_mode: "yolo_vlm",
      snapshot: { ...task().snapshot, task_id: admittedTaskId, runtime_status: "queued" },
    });
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("connection reset"))
      .mockResolvedValueOnce(response(admitted, 201))
      .mockResolvedValueOnce(response(admitted, 202));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const user = userEvent.setup();
    render(<GuidedRestartAction task={task()} disabled={false} onAdmitted={vi.fn()}
      onStarted={vi.fn()} onBusyChange={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "用原图新建复检" }));
    expect(await screen.findByRole("button", { name: "重试新建复检" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "重试新建复检" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).body)
      .toBe((fetchMock.mock.calls[1]?.[1] as RequestInit).body);
  });

  it("retries only start after admission has succeeded", async () => {
    const admitted = task({
      request_id: requestId,
      inspection_mode: "yolo_vlm",
      snapshot: { ...task().snapshot, task_id: admittedTaskId, runtime_status: "queued" },
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(admitted, 201))
      .mockRejectedValueOnce(new TypeError("connection reset"))
      .mockResolvedValueOnce(response(admitted, 202));
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(globalThis.crypto, "randomUUID").mockReturnValue(requestId);
    const user = userEvent.setup();
    render(<GuidedRestartAction task={task()} disabled={false} onAdmitted={vi.fn()}
      onStarted={vi.fn()} onBusyChange={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "用原图新建复检" }));
    await user.click(await screen.findByRole("button", { name: "重试新建复检" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    expect(fetchMock.mock.calls.filter(([url]) => url === "/api/tasks")).toHaveLength(1);
    expect(fetchMock.mock.calls[2]?.[0]).toBe(`/api/tasks/${admittedTaskId}/start`);
  });
});
