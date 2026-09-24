import { afterEach, expect, it, vi } from "vitest";
import { listSessions, updateSession } from "./api";

const summary = {
  session_id: "11111111-1111-4111-8111-111111111111", created_at: "2026-09-24T08:00:00Z",
  updated_at: "2026-09-24T09:00:00Z", title: "上游巡检", task_count: 2, turn_count: 3,
  custom_title: "上游巡检", pinned: true, archived: false,
};
const json = (value: unknown) => new Response(JSON.stringify(value), {
  headers: { "content-type": "application/json" },
});
afterEach(() => vi.unstubAllGlobals());

it("patches only the requested metadata and returns the server summary", async () => {
  const fetch = vi.fn().mockResolvedValue(json({ ...summary, archived: true }));
  vi.stubGlobal("fetch", fetch);
  const saved = await updateSession(summary.session_id, { archived: true });
  expect(saved).toMatchObject({ title: "上游巡检", pinned: true, archived: true });
  expect(fetch.mock.calls[0][0]).toBe(`/api/sessions/${summary.session_id}`);
  expect(fetch.mock.calls[0][1].method).toBe("PATCH");
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ archived: true });
});

it.each([{ archived: "false" }, { pinned: null }, { custom_title: 12 }])(
  "rejects malformed supplied session metadata %j", async (invalid) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json([{ ...summary, ...invalid }])));
    await expect(listSessions()).rejects.toThrow("服务返回的数据格式无效");
  },
);
