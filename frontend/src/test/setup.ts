import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom has no viewport media-query implementation; Ant Design subscribes to it.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  }),
});
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

// jsdom has no layout viewport. Preview tests explicitly intersect the cards they exercise.
Object.defineProperty(window, "IntersectionObserver", {
  configurable: true, writable: true,
  value: class { observe() {} unobserve() {} disconnect() {} },
});

afterEach(() => {
  cleanup();
});
