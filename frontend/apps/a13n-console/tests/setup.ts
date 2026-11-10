import { afterEach, beforeEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";
afterEach(cleanup);
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent: () => true,
  }));
  // jsdom has no layout; CodeMirror still measures ranges during its lifecycle.
  Range.prototype.getClientRects = vi.fn(() => [] as unknown as DOMRectList);
  Range.prototype.getBoundingClientRect = vi.fn(() => new DOMRect());
  Element.prototype.getAnimations = vi.fn(() => []);
  Element.prototype.scrollIntoView = vi.fn();
});
