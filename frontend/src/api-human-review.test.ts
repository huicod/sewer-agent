import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, getAsset, getReview, getVideo, submitReview } from "./api";
import type { HumanReviewSubmission } from "./types";

const taskId = "44444444-4444-4444-8444-444444444444";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("review and video HTTP boundary", () => {
  it("uses the fixed review routes and JSON submission body", async () => {
    const submission: HumanReviewSubmission = {
      request_id: "88888888-8888-4888-8888-888888888888",
      reviewer: "huicod",
      snapshot_updated_at: "2026-09-11T12:00:00Z",
      decisions: [{
        finding_id: "55555555-5555-4555-8555-555555555555",
        action: "reject",
        label: null,
        open_label: null,
        reason: "不是缺陷",
      }],
      negative_audit_complete: false,
      note: "",
    };
    const validSnapshot = {
      schema_version: "1", task_id: taskId, runtime_status: "completed", inspection_result: "normal",
      stage: "finished", stage_progress: 1, overall_progress: 1, processed_frames: 1, processed_chunks: 0,
      can_cancel: false, active_tool: null, retry_count: 0, waiting_reason: null,
      updated_at: "2026-09-11T12:01:00Z", findings: [],
      coverage: { sufficient_coverage: true, sufficient_quality: true, negative_audit_complete: true },
    };
    const detail = { task_id: taskId, can_review: false, snapshot: validSnapshot, record: null };
    const record = { task_id: taskId, submission, before: validSnapshot, after: validSnapshot, reviewed_at: "2026-09-11T12:01:00Z" };
    const video = { task_id: taskId, scan: null, evidence: null };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(detail), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(record), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(video), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await getReview(taskId);
    await submitReview(taskId, submission);
    await getVideo(taskId);

    expect(fetchMock.mock.calls).toEqual([
      [`/api/tasks/${taskId}/review`, undefined],
      [`/api/tasks/${taskId}/review`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(submission),
      }],
      [`/api/tasks/${taskId}/video`, undefined],
    ]);
  });

  it("rejects malformed accepted audit and video event intervals", async () => {
    const malformedReview = { task_id: taskId, can_review: false, snapshot: {}, record: null };
    const malformedVideo = {
      task_id: taskId,
      scan: {
        state: {
          source: { asset_id: "11111111-1111-4111-8111-111111111111", version: 1 },
          config: { policy_version: "video-events-v1", iou_threshold: 0.3, max_gap_frames: 3, max_gap_seconds: 1, evidence_separation_seconds: 0.25, audit_interval_seconds: 10 },
          detector_id: "detector", model_version: "model", label_space_version: "labels",
          next_frame: 20, last_timestamp_seconds: 4, adequate_frames: 20, active: [],
          events: [{
            event_id: "bad-order", label: "BX", start_frame: 12, end_frame: 10,
            start_seconds: 4, end_seconds: 2, observations: 1,
            last_region: { width: 10, height: 10, bbox_xyxy: [1, 1, 2, 2] },
            evidence: [],
          }],
          audit_frames: [], finalized: true,
        },
        decode_complete: true, error_code: null, inspection_result: "pending",
      },
      evidence: null,
    };
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(malformedReview), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(malformedVideo), { status: 200 })));

    const invalid = { name: "ApiError", status: 200, message: "服务返回的数据格式无效" };
    await expect(getReview(taskId)).rejects.toEqual(expect.objectContaining<ApiError>(invalid));
    await expect(getVideo(taskId)).rejects.toEqual(expect.objectContaining<ApiError>(invalid));
  });

  it("loads source asset metadata through the existing intake route", async () => {
    const asset = {
      ref: { asset_id: "11111111-1111-4111-8111-111111111111", version: 2 },
      kind: "video",
      media_type: "video/mp4",
      byte_size: 2048,
      width: 640,
      height: 480,
      duration_seconds: 12.5,
      parent: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(asset), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getAsset(asset.ref)).resolves.toEqual(asset);
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/assets/${asset.ref.asset_id}/versions/2`,
      undefined,
    );
  });

  it("accepts the backend contract for confirming an existing named OTHER finding", async () => {
    const submission: HumanReviewSubmission = {
      request_id: "88888888-8888-4888-8888-888888888888",
      reviewer: "huicod",
      snapshot_updated_at: "2026-09-11T12:00:00Z",
      decisions: [{
        finding_id: "55555555-5555-4555-8555-555555555555",
        action: "confirm",
        label: null,
        open_label: "沉积物",
        reason: "名称已核实",
      }],
      negative_audit_complete: false,
      note: "",
    };
    const snapshot = {
      schema_version: "1", task_id: taskId, runtime_status: "completed", inspection_result: "defects_found",
      stage: "finished", stage_progress: 1, overall_progress: 1, processed_frames: 1, processed_chunks: 0,
      can_cancel: false, active_tool: null, retry_count: 0, waiting_reason: null,
      updated_at: "2026-09-11T12:01:00Z", findings: [],
      coverage: { sufficient_coverage: true, sufficient_quality: true, negative_audit_complete: false },
    };
    const record = { task_id: taskId, submission, before: snapshot, after: snapshot, reviewed_at: "2026-09-11T12:01:00Z" };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(record), { status: 200 })));

    await expect(submitReview(taskId, submission)).resolves.toEqual(record);
  });

  it("rejects review audit timestamps without timezone information", async () => {
    const snapshot = {
      schema_version: "1", task_id: taskId, runtime_status: "completed", inspection_result: "normal",
      stage: "finished", stage_progress: 1, overall_progress: 1, processed_frames: 1, processed_chunks: 0,
      can_cancel: false, active_tool: null, retry_count: 0, waiting_reason: null,
      updated_at: "2026-09-11T12:01:00Z", findings: [],
      coverage: { sufficient_coverage: true, sufficient_quality: true, negative_audit_complete: true },
    };
    const submission = {
      request_id: "88888888-8888-4888-8888-888888888888", reviewer: "huicod",
      snapshot_updated_at: "2026-09-11T12:00:00", decisions: [],
      negative_audit_complete: true, note: "",
    };
    const record = { task_id: taskId, submission, before: snapshot, after: snapshot, reviewed_at: "2026-09-11T12:01:00Z" };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(record), { status: 200 })));

    await expect(submitReview(taskId, submission)).rejects.toEqual(expect.objectContaining<ApiError>({
      name: "ApiError", status: 200, message: "服务返回的数据格式无效",
    }));
  });
});
