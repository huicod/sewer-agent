import { fireEvent, render, screen } from "@testing-library/react";
import { useLayoutEffect, useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAssistantResize } from "./useAssistantResize";

const STORAGE_KEY = "test.assistant-width";
let resizeCallback: ResizeObserverCallback | undefined;

class TestResizeObserver implements ResizeObserver {
  constructor(callback: ResizeObserverCallback) {
    resizeCallback = callback;
  }
  observe() {}
  unobserve() {}
  disconnect() {}
}

function Harness({ containerWidth = 1200 }: { containerWidth?: number }) {
  const workspaceRef = useRef<HTMLDivElement>(null);
  const resize = useAssistantResize(workspaceRef, { storageKey: STORAGE_KEY });

  useLayoutEffect(() => {
    if (!workspaceRef.current) return;
    Object.defineProperty(workspaceRef.current, "clientWidth", {
      configurable: true,
      value: containerWidth,
    });
    resizeCallback?.([], {} as ResizeObserver);
  }, [containerWidth, workspaceRef]);

  return (
    <div ref={workspaceRef}>
      <output aria-label="assistant width">{resize.width}</output>
      <div aria-label="resize assistant" {...resize.separatorProps} />
    </div>
  );
}

function width() {
  return Number(screen.getByLabelText("assistant width").textContent);
}

describe("useAssistantResize", () => {
  beforeEach(() => {
    localStorage.clear();
    resizeCallback = undefined;
    vi.stubGlobal("ResizeObserver", TestResizeObserver);
  });

  afterEach(() => vi.unstubAllGlobals());

  it("grows the right pane when its separator is dragged left and persists it", () => {
    render(<Harness />);
    const separator = screen.getByRole("separator", { name: "resize assistant" });

    fireEvent.pointerDown(separator, { pointerId: 1, clientX: 800 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 700 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 700 });

    expect(width()).toBe(520);
    expect(separator.getAttribute("aria-valuenow")).toBe("520");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("520");
  });

  it("supports directional and boundary keyboard resizing", () => {
    render(<Harness containerWidth={1000} />);
    const separator = screen.getByRole("separator", { name: "resize assistant" });

    fireEvent.keyDown(separator, { key: "ArrowLeft" });
    expect(width()).toBe(436);
    fireEvent.keyDown(separator, { key: "ArrowRight" });
    expect(width()).toBe(420);
    fireEvent.keyDown(separator, { key: "Home" });
    expect(width()).toBe(320);
    fireEvent.keyDown(separator, { key: "End" });
    expect(width()).toBe(632);
  });

  it("ignores invalid persistence and clamps a valid preference to available space", () => {
    localStorage.setItem(STORAGE_KEY, "not-a-width");
    const { unmount } = render(<Harness containerWidth={800} />);
    expect(width()).toBe(420);

    unmount();
    localStorage.setItem(STORAGE_KEY, "700");
    render(<Harness containerWidth={800} />);
    expect(width()).toBe(432);
  });

  it("restores the preferred width after a temporary container constraint", () => {
    localStorage.setItem(STORAGE_KEY, "700");
    const { rerender } = render(<Harness containerWidth={800} />);
    expect(width()).toBe(432);

    rerender(<Harness containerWidth={1200} />);
    expect(width()).toBe(700);
    expect(localStorage.getItem(STORAGE_KEY)).toBe("700");
  });

  it("stops an active drag on pointer cancellation and unmount", () => {
    const { unmount } = render(<Harness />);
    const separator = screen.getByRole("separator", { name: "resize assistant" });
    fireEvent.pointerDown(separator, { pointerId: 4, clientX: 800 });
    fireEvent.pointerCancel(window, { pointerId: 4 });
    fireEvent.pointerMove(window, { pointerId: 4, clientX: 600 });
    expect(width()).toBe(420);

    fireEvent.pointerDown(separator, { pointerId: 5, clientX: 800 });
    unmount();
    expect(() => fireEvent.pointerMove(window, { pointerId: 5, clientX: 600 })).not.toThrow();
  });
});
