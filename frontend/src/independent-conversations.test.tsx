import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const firstId = "11111111-1111-4111-8111-111111111111";
const secondId = "22222222-2222-4222-8222-222222222222";
const thirdId = "33333333-3333-4333-8333-333333333333";

const summaries = [
  {
    session_id: firstId,
    created_at: "2026-09-24T08:00:00Z",
    updated_at: "2026-09-24T09:00:00Z",
    title: "第一段检查",
    task_count: 1,
    turn_count: 2,
  },
  {
    session_id: secondId,
    created_at: "2026-09-24T08:30:00Z",
    updated_at: "2026-09-24T10:00:00Z",
    title: "第二段检查",
    task_count: 2,
    turn_count: 3,
  },
];

vi.mock("./InspectionConversation", () => ({
  default: ({ sessionId, onSummary, onToggleNavigation, navigationOpen }: {
    sessionId?: string;
    onSummary?: (summary: (typeof summaries)[number]) => void;
    onToggleNavigation?: () => void;
    navigationOpen?: boolean;
  }) => (
    <div>
      <span data-testid="active-conversation">{sessionId}</span>
      <span data-testid="navigation-state">{String(navigationOpen)}</span>
      <button type="button" onClick={onToggleNavigation}>子组件打开导航</button>
      <button type="button" onClick={() => onSummary?.({
        ...summaries[0], title: "刚刚更新", updated_at: "2026-09-24T12:00:00Z",
      })}>更新摘要</button>
    </div>
  ),
}));

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function setMobileViewport(mobile: boolean) {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({
    matches: mobile,
    media: "(max-width: 900px)",
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
}

beforeEach(() => {
  localStorage.clear();
  setMobileViewport(false);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("independent conversation shell", () => {
  it("restores a saved session, switches identity, and persists the selection", async () => {
    localStorage.setItem("pipe-inspector.session-id", firstId);
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe("/api/sessions");
      return json(summaries);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<App pollIntervalMs={60_000} />);
    expect((await screen.findByTestId("active-conversation")).textContent).toBe(firstId);
    expect(document.querySelector(".conversation-shell")?.className)
      .toContain("conversation-shell--navigation");
    await user.click(screen.getByRole("button", { name: "打开对话：第二段检查" }));

    expect(screen.getByTestId("active-conversation").textContent).toBe(secondId);
    expect(document.querySelector(".conversation-shell")?.className)
      .toContain("conversation-shell--navigation");
    expect(localStorage.getItem("pipe-inspector.session-id")).toBe(secondId);
  });

  it("creates a real session only when the list is empty and can create another once", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(json([]))
      .mockResolvedValueOnce(json({ session_id: firstId, created_at: summaries[0].created_at }, 201))
      .mockResolvedValueOnce(json({ session_id: thirdId, created_at: "2026-09-24T13:00:00Z" }, 201));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(<App />);
    expect((await screen.findByTestId("active-conversation")).textContent).toBe(firstId);
    await user.click(screen.getByRole("button", { name: "新建对话" }));

    expect((await screen.findByTestId("active-conversation")).textContent).toBe(thirdId);
    expect(fetchMock.mock.calls.map((call) => [call[0], call[1]?.method])).toEqual([
      ["/api/sessions", undefined],
      ["/api/sessions", "POST"],
      ["/api/sessions", "POST"],
    ]);
  });

  it("moves an updated summary to the front without changing the active conversation", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(summaries)));
    const user = userEvent.setup();
    render(<App />);
    await screen.findByTestId("active-conversation");

    await user.click(screen.getByRole("button", { name: "更新摘要" }));

    const rows = screen.getAllByRole("button", { name: /打开对话：/ });
    expect(rows[0].getAttribute("aria-label")).toBe("打开对话：刚刚更新");
    expect(screen.getByTestId("active-conversation").textContent).toBe(firstId);
  });

  it("keeps the chosen session when a stale refresh resolves", async () => {
    let resolveRefresh!: (response: Response) => void;
    const refresh = new Promise<Response>((resolve) => { resolveRefresh = resolve; });
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(json(summaries))
      .mockReturnValueOnce(refresh));
    const user = userEvent.setup();
    render(<App />);
    await screen.findByTestId("active-conversation");

    await user.click(screen.getByRole("button", { name: "打开对话：第一段检查" }));
    await user.click(screen.getByRole("button", { name: "刷新对话列表" }));
    resolveRefresh(json([summaries[1]]));

    await waitFor(() => expect(screen.getByTestId("active-conversation").textContent).toBe(firstId));
  });

  it("opens and closes mobile navigation with Escape and returns focus", async () => {
    setMobileViewport(true);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(summaries)));
    const user = userEvent.setup();
    render(<App />);
    await screen.findByTestId("active-conversation");
    const toggle = screen.getByRole("button", { name: "打开对话导航" });

    await user.click(toggle);
    expect(screen.getByRole("complementary", { name: "对话导航" }).closest("div")?.className)
      .toContain("conversation-nav--open");
    await user.keyboard("{Escape}");

    expect(document.activeElement).toBe(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
  });
});
