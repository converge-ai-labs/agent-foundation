// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { fitVisualViewport } from "./visual-viewport";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
it("fits keyboard resize and pan without replacing content, and releases listeners on cleanup", () => {
  vi.useFakeTimers();
  vi.stubGlobal("requestAnimationFrame", (fn: FrameRequestCallback) =>
    setTimeout(fn, 1),
  );
  vi.stubGlobal("cancelAnimationFrame", clearTimeout);
  const viewport = Object.assign(new EventTarget(), {
    height: 800,
    offsetTop: 0,
    scale: 1,
  });
  vi.stubGlobal("visualViewport", viewport);
  vi.stubGlobal("innerWidth", 390);
  const shell = document.createElement("div");
  const editor = document.createElement("textarea");
  editor.value = "Shared draft remains here";
  shell.append(editor);
  const close = fitVisualViewport(shell);
  vi.runAllTimers();
  expect(shell.style.getPropertyValue("--visible-height")).toBe("800px");
  viewport.height = 430;
  viewport.offsetTop = 18;
  viewport.dispatchEvent(new Event("resize"));
  viewport.dispatchEvent(new Event("scroll"));
  vi.runAllTimers();
  expect(shell.style.getPropertyValue("--visible-height")).toBe("430px");
  expect(shell.style.getPropertyValue("--visible-top")).toBe("18px");
  expect(shell.firstChild).toBe(editor);
  expect(editor.value).toBe("Shared draft remains here");
  viewport.scale = 2;
  viewport.dispatchEvent(new Event("resize"));
  vi.runAllTimers();
  expect(shell.style.getPropertyValue("--visible-height")).toBe("");
  viewport.scale = 1;
  vi.stubGlobal("innerWidth", 1200);
  window.dispatchEvent(new Event("resize"));
  vi.runAllTimers();
  expect(shell.style.getPropertyValue("--visible-top")).toBe("");
  close();
  vi.stubGlobal("innerWidth", 390);
  viewport.dispatchEvent(new Event("resize"));
  vi.runAllTimers();
  expect(shell.style.getPropertyValue("--visible-height")).toBe("");
});
