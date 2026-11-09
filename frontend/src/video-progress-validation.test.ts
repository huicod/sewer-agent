import { afterEach, describe, expect, it, vi } from "vitest";

import { getVideo } from "./api";
import type { VideoAggregationState, VideoDetail } from "./types";

const taskId = "44444444-4444-4444-8444-444444444444";
const otherTaskId = "55555555-5555-4555-8555-555555555555";
const source = { asset_id: "11111111-1111-4111-8111-111111111111", version: 1 };
const invalidResponse = { name: "ApiError", status: 200, message: "服务返回的数据格式无效" };

function state(): VideoAggregationState {
  return {
    source: { ...source },
    config: {
      policy_version: "video-events-v1", iou_threshold: 0.3, max_gap_frames: 3,
      max_gap_seconds: 1, evidence_separation_seconds: 0.25, audit_interval_seconds: 10,
    },
    detector_id: "primary", model_version: "test-model", label_space_version: "five-labels",
    next_frame: 8, last_timestamp_seconds: 7.8, adequate_frames: 6,
    active: [], events: [], audit_frames: [], finalized: false,
  };
}

function detail(): VideoDetail {
  return {
    task_id: taskId, scan: null, scan_progress: state(), scan_start_seconds: 7.1,
    evidence: { frames: [], review_frames: [] },
  };
}

function respond(payload: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("video progress HTTP boundary", () => {
  it.each([null, {
    state: { ...state(), finalized: true },
    decode_complete: true, error_code: null, inspection_result: "pending",
  }])("keeps historical video responses without progress fields readable: %j", async (scan) => {
    const legacy = { task_id: taskId, scan, evidence: null };
    const fetchMock = respond(legacy);

    await expect(getVideo(taskId, source)).resolves.toEqual(legacy);
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(`/api/tasks/${taskId}/video`, undefined);
  });

  it("preserves the real in-flight state and nonzero original start timestamp", async () => {
    const payload = detail();
    const fetchMock = respond(payload);

    await expect(getVideo(taskId, source)).resolves.toEqual(payload);
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(`/api/tasks/${taskId}/video`, undefined);
  });

  it("accepts an unstarted response with explicit null progress and start", async () => {
    const payload = { ...detail(), scan_progress: null, scan_start_seconds: null };
    respond(payload);
    await expect(getVideo(taskId, source)).resolves.toEqual(payload);
  });

  it("accepts a final single-frame scan whose first and last timestamps match", async () => {
    const payload: VideoDetail = { ...detail(), scan_progress: null, scan_start_seconds: 7.1, scan: {
      state: { ...state(), finalized: true, next_frame: 1, adequate_frames: 1, last_timestamp_seconds: 7.1 },
      decode_complete: true, error_code: null, inspection_result: "pending",
    } };
    respond(payload);
    await expect(getVideo(taskId, source)).resolves.toEqual(payload);
  });

  it.each([
    { next_frame: -1 }, { next_frame: 1.5 }, { adequate_frames: 9 },
    { last_timestamp_seconds: null }, { detector_id: null }, { model_version: "" },
    { label_space_version: null }, { finalized: "true" }, { audit_frames: [{}] },
  ])("rejects malformed persisted progress: %j", async (change) => {
    respond({ ...detail(), scan_progress: { ...state(), ...change } });
    await expect(getVideo(taskId, source)).rejects.toMatchObject(invalidResponse);
  });

  it("rejects another task even when its progress source matches", async () => {
    respond({ ...detail(), task_id: otherTaskId });
    await expect(getVideo(taskId, source)).rejects.toMatchObject(invalidResponse);
  });

  it.each(["scan", "scan_progress"] as const)("binds %s to the requested asset and version", async (field) => {
    for (const wrongSource of [
      { asset_id: "22222222-2222-4222-8222-222222222222", version: source.version },
      { asset_id: source.asset_id, version: 2 },
    ]) {
      const wrongState = { ...state(), source: wrongSource };
      const payload: VideoDetail = field === "scan_progress"
        ? { ...detail(), scan_progress: wrongState }
        : { ...detail(), scan_progress: null, scan: {
          state: wrongState, decode_complete: false, error_code: null, inspection_result: "pending",
        } };
      respond(payload);
      await expect(getVideo(taskId, source)).rejects.toMatchObject(invalidResponse);
    }
  });

  it.each([-0.1, "7.1", true, [], {}])("rejects an invalid start timestamp: %j", async (start) => {
    respond({ ...detail(), scan_start_seconds: start });
    await expect(getVideo(taskId, source)).rejects.toMatchObject(invalidResponse);
  });

  it("rejects a JSON number that overflows to infinity", async () => {
    const body = JSON.stringify({ ...detail(), scan_start_seconds: "overflow" })
      .replace('"overflow"', "1e400");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status: 200 })));
    await expect(getVideo(taskId, source)).rejects.toMatchObject(invalidResponse);
  });

  it("rejects a scan start after the last saved timestamp", async () => {
    respond({ ...detail(), scan_start_seconds: 7.9 });
    await expect(getVideo(taskId, source)).rejects.toMatchObject(invalidResponse);
  });

  it.each([null, {
    ...state(), next_frame: 0, adequate_frames: 0, last_timestamp_seconds: null,
    detector_id: null, model_version: null, label_space_version: null,
  }])("rejects a start timestamp without processed scan data: %j", async (progress) => {
    respond({ ...detail(), scan_progress: progress });
    await expect(getVideo(taskId, source)).rejects.toMatchObject(invalidResponse);
  });

  it("rejects simultaneous final and in-flight scan states", async () => {
    respond({ ...detail(), scan: {
      state: { ...state(), finalized: true },
      decode_complete: true, error_code: null, inspection_result: "pending",
    } });
    await expect(getVideo(taskId, source)).rejects.toMatchObject(invalidResponse);
  });
});
