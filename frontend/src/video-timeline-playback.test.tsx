import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getAsset, getVideo } from "./api";
import { TaskCard, type TaskCardProps } from "./TaskCard";
import type { AssetRecord, AssetRef, TaskRecord, VideoDetail, VideoEvent, VideoEvidenceFrame } from "./types";

vi.mock("./api", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api")>(),
  getAsset: vi.fn(),
  getVideo: vi.fn(),
}));

const firstId = "44444444-4444-4444-8444-444444444444";
const secondId = "55555555-5555-4555-8555-555555555555";
const source: AssetRef = { asset_id: "11111111-1111-4111-8111-111111111111", version: 1 };
const savedFrame: VideoEvidenceFrame = {
  frame_index: 45, timestamp_seconds: 1.8,
  asset: { asset_id: "77777777-7777-4777-8777-777777777777", version: 3 },
};
const region = { width: 640, height: 480, bbox_xyxy: [10, 20, 100, 120] as [number, number, number, number] };
const quality = { mean_luminance: 100, contrast: 30, sharpness: 100, adequate: true, reasons: [] };

function task(taskId = firstId, input = source, withVideoLocator = false): TaskRecord {
  return {
    request_id: taskId, session_id: "33333333-3333-4333-8333-333333333333",
    input_asset: input, instruction: `检查视频 ${taskId.slice(0, 8)}`, created_at: "2026-11-09T08:00:00Z",
    snapshot: {
      schema_version: "1", task_id: taskId, runtime_status: "waiting_review", inspection_result: "inconclusive",
      stage: "review", stage_progress: 1, overall_progress: 0.9, processed_frames: 225,
      processed_chunks: 3, retry_count: 0, active_tool: null, waiting_reason: null,
      updated_at: "2026-11-09T08:01:00Z",
      coverage: { sufficient_coverage: true, sufficient_quality: true, negative_audit_complete: false },
      findings: withVideoLocator ? [{
        finding_id: "66666666-6666-4666-8666-666666666666", task_id: taskId,
        canonical_label: "PL", open_label: null, source: "detector", review_status: "needs_review",
        confirmed_by: null, score: 0.7,
        evidence: [{ asset: savedFrame.asset, region, timestamp_seconds: savedFrame.timestamp_seconds, frame_index: savedFrame.frame_index }],
      }] : [],
    },
  };
}

function videoAsset(ref = source): AssetRecord {
  return { ref, kind: "video", media_type: "video/mp4", byte_size: 10000, width: 640, height: 480, parent: null, duration_seconds: 9 };
}

function videoDetail(record = task(), start = 1.25, firstTimestamp = 0): VideoDetail {
  const frame = { ...savedFrame, timestamp_seconds: firstTimestamp ? firstTimestamp + 1.8 : savedFrame.timestamp_seconds };
  const event: VideoEvent = {
    event_id: `event-${record.snapshot.task_id}-${record.input_asset.version}`, label: "PL",
    start_frame: 31, end_frame: 75, start_seconds: start, end_seconds: start + 1,
    observations: 3, last_region: region,
    evidence: [{ frame_index: frame.frame_index, timestamp_seconds: frame.timestamp_seconds,
      width: 640, height: 480, quality, detection_index: 0, region, score: 0.7 }],
  };
  return {
    task_id: record.snapshot.task_id, scan_start_seconds: firstTimestamp,
    scan: {
      state: {
        source: record.input_asset,
        config: { policy_version: "video-events-v1", iou_threshold: 0.3, max_gap_frames: 10, max_gap_seconds: 1, evidence_separation_seconds: 1, audit_interval_seconds: 5 },
        detector_id: null, model_version: null, label_space_version: null, next_frame: 225,
        last_timestamp_seconds: firstTimestamp + 8.96, adequate_frames: 225,
        active: [], events: [event], audit_frames: [], finalized: true,
      },
      decode_complete: true, error_code: null, inspection_result: "pending",
    },
    evidence: { frames: [frame], review_frames: [frame.frame_index] },
  };
}

function props(record = task()): TaskCardProps {
  return {
    task: record, presentation: "workbench", starting: false, cancelling: false,
    onStart: vi.fn(), onCancel: vi.fn(), onReviewed: vi.fn(), onSelectFrame: vi.fn(), frameSelectionDisabled: false,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => { resolve = accept; });
  return { promise, resolve };
}

function loadMetadata(player: HTMLVideoElement, duration = 9) {
  Object.defineProperty(player, "duration", { configurable: true, value: duration });
  fireEvent.loadedMetadata(player);
}

beforeEach(() => {
  vi.stubGlobal("IntersectionObserver", undefined);
  vi.mocked(getAsset).mockReset().mockImplementation(async (ref) => videoAsset(ref));
  vi.mocked(getVideo).mockReset().mockResolvedValue(videoDetail());
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("workbench original video and time axis integration", () => {
  it("links metadata and native playback to the axis, while seeking never autoplays or selects a tools frame", async () => {
    const card = props();
    const { rerender } = render(<TaskCard {...card} />);
    const player = await screen.findByLabelText<HTMLVideoElement>("输入视频预览");
    const event = await screen.findByRole("button", { name: "破裂候选 1.25 至 2.25 秒" });
    expect(player.getAttribute("src")).toBe(`/api/assets/${source.asset_id}/versions/1/content`);
    const slider = screen.getByRole<HTMLInputElement>("slider", { name: "视频播放位置" });
    expect(slider.disabled).toBe(true);
    loadMetadata(player);
    expect(slider.disabled).toBe(false);
    expect(slider.max).toBe("9");
    player.currentTime = 3.2;
    fireEvent.timeUpdate(player);
    expect(slider.value).toBe("3.2");
    expect(screen.getByLabelText("当前播放时间").textContent).toBe("00:03.20");

    fireEvent.click(event);
    expect(player.currentTime).toBe(1.25);
    expect(slider.value).toBe("1.25");
    fireEvent.click(screen.getByRole("button", { name: "定位证据帧 45，1.8 秒，已选复检" }));
    expect(player.currentTime).toBe(1.8);
    expect(screen.getByAltText("证据帧 45，1.8 秒").getAttribute("src"))
      .toBe(`/api/assets/${savedFrame.asset.asset_id}/versions/3/content`);
    fireEvent.change(slider, { target: { value: "7.35" } });
    expect(player.currentTime).toBe(7.35);
    expect(card.onSelectFrame).not.toHaveBeenCalled();
    expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "使用此证据帧" }));
    expect(card.onSelectFrame).toHaveBeenCalledExactlyOnceWith(firstId, savedFrame);
    rerender(<TaskCard {...card} frameSelectionDisabled />);
    fireEvent.click(screen.getByRole("button", { name: "使用此证据帧" }));
    expect(card.onSelectFrame).toHaveBeenCalledTimes(1);
    expect(card.onStart).not.toHaveBeenCalled();
    expect(card.onCancel).not.toHaveBeenCalled();
    expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled();
  });

  it("keeps nonzero source timestamps browseable but disables unverified playback mapping", async () => {
    const card = props();
    const detail = videoDetail(card.task, 8, 7.1);
    vi.mocked(getVideo).mockResolvedValue(detail);
    render(<TaskCard {...card} />);
    const player = await screen.findByLabelText<HTMLVideoElement>("输入视频预览");
    await screen.findByRole("button", { name: "破裂候选 8 至 9 秒" });
    loadMetadata(player);
    player.currentTime = 2;
    fireEvent.timeUpdate(player);
    expect(screen.getByRole<HTMLInputElement>("slider").disabled).toBe(true);
    expect(screen.getByText(/此视频保留了原始时间偏移/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "破裂候选 8 至 9 秒" }));
    fireEvent.click(screen.getByRole("button", { name: /^定位证据帧 45/ }));
    expect(player.currentTime).toBe(2);
    expect(card.onSelectFrame).not.toHaveBeenCalled();
    expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "使用此证据帧" }));
    expect(card.onSelectFrame).toHaveBeenCalledExactlyOnceWith(firstId, detail.evidence!.frames[0]);
  });

  it("disables linked seeking and clears playback state when the original video fails", async () => {
    const card = props();
    render(<TaskCard {...card} />);
    const player = await screen.findByLabelText<HTMLVideoElement>("输入视频预览");
    await screen.findByRole("button", { name: "破裂候选 1.25 至 2.25 秒" });
    loadMetadata(player);
    player.currentTime = 4;
    fireEvent.timeUpdate(player);
    expect(screen.getByRole<HTMLInputElement>("slider").disabled).toBe(false);
    fireEvent.error(player);
    expect(screen.queryByLabelText("输入视频预览")).toBeNull();
    expect(screen.getByText(/预览暂不可用，可打开输入文件查看/)).toBeTruthy();
    expect(screen.getByRole<HTMLInputElement>("slider").disabled).toBe(true);
    expect(screen.getByLabelText("当前播放时间").textContent).toBe("00:00.00");
    fireEvent.click(screen.getByRole("button", { name: "定位证据帧 45，1.8 秒，已选复检" }));
    expect(screen.getByAltText("证据帧 45，1.8 秒")).toBeTruthy();
    expect(card.onSelectFrame).not.toHaveBeenCalled();
    expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled();
  });

  it.each(["task", "source version"] as const)("ignores the old video-detail response when switching %s", async (change) => {
    const first = task(firstId, source, true);
    const next = change === "task" ? task(secondId, source, true) : task(firstId, { ...source, version: 2 }, true);
    const oldRequest = deferred<VideoDetail>();
    const newRequest = deferred<VideoDetail>();
    vi.mocked(getVideo).mockImplementation((id, ref) =>
      (change === "task" ? id === firstId : ref?.version === 1) ? oldRequest.promise : newRequest.promise);
    const card = props(first);
    const { rerender } = render(<TaskCard {...card} />);
    await waitFor(() => expect(getVideo).toHaveBeenCalledWith(firstId, first.input_asset));
    rerender(<TaskCard {...card} task={next} />);
    await waitFor(() => expect(getVideo).toHaveBeenCalledWith(next.snapshot.task_id, next.input_asset));
    await act(async () => { newRequest.resolve(videoDetail(next, 5)); await newRequest.promise; });
    expect(await screen.findByRole("button", { name: "破裂候选 5 至 6 秒" })).toBeTruthy();
    await act(async () => { oldRequest.resolve(videoDetail(first, 1)); await oldRequest.promise; });
    expect(screen.queryByRole("button", { name: "破裂候选 1 至 2 秒" })).toBeNull();
    expect(screen.getByRole("button", { name: "破裂候选 5 至 6 秒" })).toBeTruthy();
    expect(card.onSelectFrame).not.toHaveBeenCalled();
  });

  it("loads a different task on the same video even when there are no findings to identify its media", async () => {
    const first = task();
    const next = task(secondId);
    vi.mocked(getVideo).mockImplementation(async (id) => videoDetail(id === firstId ? first : next, id === firstId ? 1 : 5));
    const card = props(first);
    const { rerender } = render(<TaskCard {...card} />);
    await screen.findByRole("button", { name: "破裂候选 1 至 2 秒" });
    rerender(<TaskCard {...card} task={next} />);
    expect(await screen.findByRole("button", { name: "破裂候选 5 至 6 秒" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "破裂候选 1 至 2 秒" })).toBeNull();
    expect(getVideo).toHaveBeenCalledWith(secondId, source);
  });

  it("clears already loaded evidence when the source version changes at the same snapshot revision", async () => {
    const first = task(firstId, source, true);
    const next = task(firstId, { ...source, version: 2 }, true);
    const newRequest = deferred<VideoDetail>();
    vi.mocked(getVideo).mockImplementation((_id, ref) => ref?.version === 1 ? Promise.resolve(videoDetail(first, 1)) : newRequest.promise);
    const card = props(first);
    const { rerender } = render(<TaskCard {...card} />);
    fireEvent.click(await screen.findByRole("button", { name: "定位证据帧 45，1.8 秒，已选复检" }));
    expect(screen.getByAltText("证据帧 45，1.8 秒")).toBeTruthy();
    rerender(<TaskCard {...card} task={next} />);
    await waitFor(() => expect(getVideo).toHaveBeenCalledWith(firstId, next.input_asset));
    expect(screen.queryByRole("button", { name: "破裂候选 1 至 2 秒" })).toBeNull();
    expect(screen.queryByAltText("证据帧 45，1.8 秒")).toBeNull();
    await act(async () => { newRequest.resolve(videoDetail(next, 5)); await newRequest.promise; });
    expect(await screen.findByRole("button", { name: "破裂候选 5 至 6 秒" })).toBeTruthy();
    expect(card.onSelectFrame).not.toHaveBeenCalled();
  });

  it("ignores old source metadata after switching to another video", async () => {
    const oldMetadata = deferred<AssetRecord>();
    const nextSource = { asset_id: "99999999-9999-4999-8999-999999999999", version: 2 };
    const next = task(secondId, nextSource);
    vi.mocked(getAsset).mockImplementation((ref) => ref.asset_id === source.asset_id ? oldMetadata.promise : Promise.resolve(videoAsset(ref)));
    vi.mocked(getVideo).mockResolvedValue(videoDetail(next, 5));
    const card = props();
    const { rerender } = render(<TaskCard {...card} />);
    await waitFor(() => expect(getAsset).toHaveBeenCalledWith(source));
    rerender(<TaskCard {...card} task={next} />);
    const player = await screen.findByLabelText<HTMLVideoElement>("输入视频预览");
    await screen.findByRole("button", { name: "破裂候选 5 至 6 秒" });
    await act(async () => {
      oldMetadata.resolve({ ...videoAsset(source), kind: "image", media_type: "image/jpeg", duration_seconds: null });
      await oldMetadata.promise;
    });
    expect(player.getAttribute("src")).toBe(`/api/assets/${nextSource.asset_id}/versions/2/content`);
    expect(screen.queryByAltText("检查任务的输入图片")).toBeNull();
    expect(screen.getByRole("button", { name: "破裂候选 5 至 6 秒" })).toBeTruthy();
    expect(getVideo).not.toHaveBeenCalledWith(firstId, source);
  });
});
