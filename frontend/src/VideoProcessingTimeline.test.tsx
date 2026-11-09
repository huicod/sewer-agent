import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { VideoProcessingTimeline, type VideoProcessingTimelineProps } from "./VideoProcessingTimeline";
import type { VideoAggregationState, VideoEvent, VideoEvidenceFrame } from "./types";

const source = { asset_id: "original-video", version: 1 };
const region = { width: 640, height: 480, bbox_xyxy: [10, 20, 100, 120] as [number, number, number, number] };
const quality = { mean_luminance: 100, contrast: 30, sharpness: 100, adequate: true, reasons: [] };

function candidate(id: string, start: number, end: number): VideoEvent {
  return {
    event_id: id, label: "PL", start_frame: 12, end_frame: 40,
    start_seconds: start, end_seconds: end, observations: 3,
    last_region: region, evidence: [],
  };
}

function scan(overrides: Partial<VideoAggregationState> = {}): VideoAggregationState {
  return {
    source,
    config: { policy_version: "video-events-v1", iou_threshold: 0.3, max_gap_frames: 10, max_gap_seconds: 1, evidence_separation_seconds: 1, audit_interval_seconds: 5 },
    detector_id: null, model_version: null, label_space_version: null,
    next_frame: 101, last_timestamp_seconds: 4, adequate_frames: 100,
    active: [], events: [], audit_frames: [], finalized: false, ...overrides,
  };
}

const savedFrame: VideoEvidenceFrame = {
  frame_index: 42, timestamp_seconds: 4.25, asset: { asset_id: "saved-evidence", version: 2 },
};

const defaults: VideoProcessingTimelineProps = {
  state: scan(), frames: [], reviewFrames: [], durationSeconds: 10,
  currentTime: 2, processedFrames: 100, decodeComplete: false, scanStartSeconds: 0,
};

describe("video processing time axis", () => {
  it("draws the persisted scan span and exposes a native seek control", () => {
    const onSeek = vi.fn();
    const { container } = render(<VideoProcessingTimeline {...defaults} onSeek={onSeek} />);
    const span = container.querySelector<HTMLElement>(".processing-timeline__scanned")!;
    expect(span.style.width).toBe("40%");
    expect(span.style.left).toBe("0%");
    expect(screen.getByText("已处理 101 帧")).toBeTruthy();
    expect(screen.getByRole("img", { name: "已保存扫描范围：0 至 4 秒" })).toBeTruthy();
    const slider = screen.getByRole("slider", { name: "视频播放位置" });
    expect(slider.getAttribute("max")).toBe("10");
    expect(slider.getAttribute("value")).toBe("2");
    fireEvent.change(slider, { target: { value: "3.75" } });
    expect(onSeek).toHaveBeenLastCalledWith(3.75);
    expect(screen.queryByText("解码完整")).toBeNull();
  });

  it("does not invent a zero start when a persisted head lacks its first timestamp", () => {
    const { container } = render(<VideoProcessingTimeline {...defaults} scanStartSeconds={undefined} />);
    expect(container.querySelector(".processing-timeline__scanned")).toBeNull();
    expect(container.querySelector(".processing-timeline__scan-end")).toBeTruthy();
    expect(screen.getByRole("img", { name: "扫描位置已保存至 4 秒，起点未知" })).toBeTruthy();
  });

  it("retains nonzero original timestamps without shading or seeking an invented offset", () => {
    const onSeek = vi.fn();
    const event = candidate("offset", 8, 12);
    const reason = "视频时间戳与播放位置尚未对齐，请使用原视频播放器。";
    const { container } = render(<VideoProcessingTimeline {...defaults}
      state={scan({ last_timestamp_seconds: 16, events: [event] })}
      scanStartSeconds={7.1} durationSeconds={9} onSeek={onSeek} seekUnavailableReason={reason} />);
    const span = container.querySelector<HTMLElement>(".processing-timeline__scanned")!;
    expect(Number.parseFloat(span.style.left)).toBeCloseTo(7.1 / 16 * 100);
    expect(Number.parseFloat(span.style.width)).toBeCloseTo((16 - 7.1) / 16 * 100);
    expect(screen.getByRole("slider").getAttribute("max")).toBe("16");
    expect(screen.getByRole("slider").hasAttribute("disabled")).toBe(true);
    expect(screen.getByText(reason)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "破裂候选 8 至 12 秒" }));
    expect(onSeek).not.toHaveBeenCalled();
    expect(container.querySelector(".processing-timeline__cursor")).toBeNull();
  });

  it("labels unknown duration as an observed range and handles a missing head honestly", () => {
    const { rerender, container } = render(<VideoProcessingTimeline {...defaults}
      state={scan({ last_timestamp_seconds: 8.96 })} durationSeconds={null} />);
    expect(screen.getByText(/总时长未知/)).toBeTruthy();
    expect(screen.getByRole("slider").getAttribute("max")).toBe("8.96");
    expect(screen.getByText("刻度依据已保存的最晚时间点，不代表完整视频时长。")).toBeTruthy();
    rerender(<VideoProcessingTimeline {...defaults} state={null} durationSeconds={null} processedFrames={225} />);
    expect(screen.getByText("已处理 225 帧")).toBeTruthy();
    expect(screen.getByText("时间定位结果尚未保存")).toBeTruthy();
    expect(container.querySelector(".processing-timeline__scanned")).toBeNull();
    expect(screen.getByRole("slider").getAttribute("max")).toBe("0");
  });

  it("keeps overlapping and endpoint-only candidates individually selectable", () => {
    const onSeek = vi.fn();
    const onSelectFrame = vi.fn();
    render(<VideoProcessingTimeline {...defaults} state={scan({ events: [candidate("a", 1, 4), candidate("b", 2, 3), candidate("single", 10, 10)] })}
      onSeek={onSeek} onSelectFrame={onSelectFrame} />);
    const first = screen.getByRole("button", { name: "破裂候选 1 至 4 秒" });
    const second = screen.getByRole("button", { name: "破裂候选 2 至 3 秒" });
    expect(first.style.top).not.toBe(second.style.top);
    fireEvent.click(second);
    expect(onSeek).toHaveBeenLastCalledWith(2);
    const point = screen.getByRole("button", { name: "破裂候选 10 至 10 秒" });
    expect(point.style.left).toContain("100% - 8px");
    fireEvent.click(point);
    expect(onSeek).toHaveBeenLastCalledWith(10);
    expect(onSelectFrame).not.toHaveBeenCalled();
  });

  it("previews the exact saved asset and requires a separate explicit tools action", () => {
    const onSeek = vi.fn();
    const onSelectFrame = vi.fn();
    const event = candidate("with-frame", 4, 5);
    event.evidence = [{ frame_index: 42, timestamp_seconds: 4.25, width: 640, height: 480, quality, detection_index: 0, region, score: 0.8 }];
    const props = { ...defaults, state: scan({ events: [event] }), frames: [savedFrame], reviewFrames: [42], onSeek, onSelectFrame };
    const { rerender } = render(<VideoProcessingTimeline {...props} frameSelectionDisabled />);
    expect(screen.queryByAltText(/证据帧/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "破裂候选 4 至 5 秒" }));
    expect(onSeek).toHaveBeenLastCalledWith(4);
    const preview = screen.getByRole("img", { name: "证据帧 42，4.25 秒" });
    expect(preview.getAttribute("src")).toBe("/api/assets/saved-evidence/versions/2/content");
    expect(onSelectFrame).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "使用此证据帧" }));
    expect(onSelectFrame).not.toHaveBeenCalled();
    rerender(<VideoProcessingTimeline {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "定位证据帧 42，4.25 秒，已选复检" }));
    expect(onSeek).toHaveBeenLastCalledWith(4.25);
    expect(onSelectFrame).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "使用此证据帧" }));
    expect(onSelectFrame).toHaveBeenCalledExactlyOnceWith(savedFrame);
    fireEvent.error(preview);
    expect(screen.getByText("预览暂不可用，打开原始证据")).toBeTruthy();
    expect(screen.getByRole("link", { name: "打开原始证据" }).getAttribute("href")).toBe("/api/assets/saved-evidence/versions/2/content");
  });

  it("does not match a candidate to a same-number frame with a different saved timestamp", () => {
    const event = candidate("mismatch", 4, 5);
    event.evidence = [{ frame_index: 42, timestamp_seconds: 4.24, width: 640, height: 480, quality, detection_index: 0, region, score: 0.8 }];
    render(<VideoProcessingTimeline {...defaults} state={scan({ events: [event] })} frames={[savedFrame]} />);
    fireEvent.click(screen.getByRole("button", { name: "破裂候选 4 至 5 秒" }));
    expect(screen.queryByAltText(/证据帧/)).toBeNull();
    expect(screen.getByText("该候选尚无已保存的证据帧。")).toBeTruthy();
  });

  it("drops active candidates after finalization without declaring defects confirmed", () => {
    const event = candidate("active", 1, 2);
    const { rerender } = render(<VideoProcessingTimeline {...defaults} state={scan({ active: [event] })} />);
    expect(screen.getByRole("button", { name: "破裂候选 1 至 2 秒" })).toBeTruthy();
    rerender(<VideoProcessingTimeline {...defaults} state={scan({ active: [event], finalized: true })} decodeComplete />);
    expect(screen.queryByRole("button", { name: "破裂候选 1 至 2 秒" })).toBeNull();
    expect(screen.getByText("解码完整")).toBeTruthy();
    expect(screen.getByText(/候选与复检入选均不代表已确认缺陷/)).toBeTruthy();
  });

  it("clears old evidence selection when the original video version changes", () => {
    const { rerender } = render(<VideoProcessingTimeline {...defaults} frames={[savedFrame]} />);
    fireEvent.click(screen.getByRole("button", { name: "定位证据帧 42，4.25 秒" }));
    expect(screen.getByAltText("证据帧 42，4.25 秒")).toBeTruthy();
    rerender(<VideoProcessingTimeline {...defaults} state={scan({ source: { ...source, version: 2 } })} frames={[savedFrame]} />);
    expect(screen.queryByAltText("证据帧 42，4.25 秒")).toBeNull();
  });

  it("bounds visible markers and previews while retaining selection of every saved frame", () => {
    const frames = Array.from({ length: 300 }, (_, index) => ({ frame_index: index, timestamp_seconds: index, asset: { asset_id: `frame-${index}`, version: 1 } }));
    render(<VideoProcessingTimeline {...defaults} state={scan({ last_timestamp_seconds: 299 })} frames={frames} durationSeconds={300} />);
    expect(screen.getAllByRole("button", { name: /定位证据帧/ })).toHaveLength(80);
    expect(screen.queryAllByAltText(/证据帧/)).toHaveLength(0);
    const selector = screen.getByRole("combobox", { name: "浏览已存证据帧" });
    expect(within(selector).getAllByRole("option")).toHaveLength(301);
    fireEvent.change(selector, { target: { value: "frame-153:1:153" } });
    expect(screen.getAllByAltText(/证据帧/)).toHaveLength(1);
    expect(screen.getByAltText("证据帧 153，153 秒").getAttribute("src")).toBe("/api/assets/frame-153/versions/1/content");
  });
});

