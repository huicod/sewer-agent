import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { VideoTimeline } from "./VideoTimeline";
import { isVideoDetail } from "./validators";

const taskId = "44444444-4444-4444-8444-444444444444";

function detail() {
  return {
    task_id: taskId,
    scan: null,
    evidence: {
      frames: [10, 20, 30, 40].map((frame_index) => ({
        frame_index, timestamp_seconds: frame_index / 5,
        asset: { asset_id: "77777777-7777-4777-8777-777777777777", version: 1 },
      })),
      review_frames: [30],
      selection: {
        version: "video-selection-v1", candidate_count: 4,
        pool_frames: [10, 20, 30, 40], review_frames: [30], deferred_count: 3,
        unreviewed_event_count: 2, unreviewed_audit_count: 1,
        scene_calls: 1, scene_errors: [] as string[],
        assessments: [
          { frame_index: 10, scene: "outside", reason: "画面是井外草地" },
          { frame_index: 20, scene: "unknown", reason: "暗部管壁仍可能有缺陷" },
          { frame_index: 30, scene: "pipe", reason: "可见管内壁" },
          { frame_index: 40, scene: "unassessable", reason: "画面完全遮挡" },
        ],
      },
    },
  };
}

afterEach(() => { vi.unstubAllGlobals(); });

it("shows selected versus deferred evidence without claiming completed review or deleting unknowns", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(detail()))));
  render(<VideoTimeline taskId={taskId} snapshotRevision="2026-09-14T12:00:00Z" />);
  await userEvent.click(screen.getByRole("button", { name: "展开视频候选时间线" }));
  await screen.findByRole("heading", { name: "复检证据选择" });
  expect(screen.getByText("待复检候选 4 帧；已选详细复检 1 帧；未入选 3 帧。")).toBeTruthy();
  expect(screen.getByText("所需复检未完整入选：2 个事件、1 张周期审计帧。")).toBeTruthy();
  expect(screen.getByText(/帧 20 · 场景不确定 · 未入选/)).toBeTruthy();
  expect(screen.getByText(/暗部管壁仍可能有缺陷/)).toBeTruthy();
  expect(screen.queryByText("已完成详细复检")).toBeNull();
});

it("discloses the cached scene failure fallback", async () => {
  const value = detail();
  value.evidence.selection.scene_errors = ["timeout"];
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(value))));
  render(<VideoTimeline taskId={taskId} snapshotRevision="2026-09-14T12:00:00Z" />);
  await userEvent.click(screen.getByRole("button", { name: "展开视频候选时间线" }));
  expect(await screen.findByText("场景筛选部分未完成，已按未知场景回退：timeout")).toBeTruthy();
});

it.each([
  { review_frames: [99] }, { pool_frames: [10, 10, 30, 40] },
  { deferred_count: 0 }, { scene_calls: 5 },
  { assessments: [{ frame_index: 99, scene: "pipe", reason: "越界" }] },
  { assessments: [{ frame_index: 10, scene: "normal", reason: "无依据结论" }] },
])("rejects malformed selection payloads: %j", (change) => {
  const value = detail();
  Object.assign(value.evidence.selection, change);
  expect(isVideoDetail(value)).toBe(false);
});

it("keeps pre-selection-version video responses readable", () => {
  expect(isVideoDetail({ task_id: taskId, scan: null, evidence: { frames: [], review_frames: [] } })).toBe(true);
});
