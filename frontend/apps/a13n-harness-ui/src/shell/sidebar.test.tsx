// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { WorkbenchSidebar } from "./sidebar";

const preference = "a13n-harness-ui.sidebar-width";

function viewport(width: number) {
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    value: width,
  });
  act(() => window.dispatchEvent(new Event("resize")));
}

function fixture() {
  const view = render(
    <WorkbenchSidebar collapsed={false}>Navigation</WorkbenchSidebar>,
  );
  const handle = screen.getByRole("separator", { name: "Resize navigation" });
  handle.setPointerCapture = vi.fn();
  handle.releasePointerCapture = vi.fn();
  return { ...view, handle };
}

function pointer(element: HTMLElement, type: string, x: number, pointerId = 1) {
  const event = new MouseEvent(type, {
    bubbles: true,
    cancelable: true,
    clientX: x,
    button: 0,
  });
  Object.defineProperties(event, {
    pointerId: { value: pointerId },
    isPrimary: { value: true },
  });
  fireEvent(element, event);
}

beforeEach(() => {
  localStorage.clear();
  viewport(1280);
});
afterEach(cleanup);

it("resizes within bounds, captures the pointer and remembers the released width", () => {
  const { handle, unmount } = fixture();
  pointer(handle, "pointerdown", 220);
  expect(handle.setPointerCapture).toHaveBeenCalledWith(1);
  pointer(handle, "pointermove", 900);
  expect(handle.getAttribute("aria-valuenow")).toBe("360");
  pointer(handle, "pointermove", 0);
  expect(handle.getAttribute("aria-valuenow")).toBe("200");
  pointer(handle, "pointermove", 280);
  expect(handle.getAttribute("aria-valuenow")).toBe("280");
  expect(localStorage.getItem(preference)).toBe("220");
  pointer(handle, "pointerup", 280);
  expect(handle.releasePointerCapture).toHaveBeenCalledWith(1);
  expect(localStorage.getItem(preference)).toBe("280");
  unmount();
  expect(fixture().handle.getAttribute("aria-valuenow")).toBe("280");
});

it("preserves the chosen width across collapse and temporary viewport constraints", () => {
  localStorage.setItem(preference, "360");
  const { handle, rerender } = fixture();
  viewport(800);
  expect(handle.getAttribute("aria-valuemax")).toBe("320");
  expect(handle.getAttribute("aria-valuenow")).toBe("320");
  expect(localStorage.getItem(preference)).toBe("360");
  rerender(<WorkbenchSidebar collapsed>Navigation</WorkbenchSidebar>);
  expect(screen.queryByRole("separator")).toBeNull();
  viewport(1280);
  rerender(<WorkbenchSidebar collapsed={false}>Navigation</WorkbenchSidebar>);
  expect(handle.getAttribute("aria-valuenow")).toBe("360");
});

it("supports keyboard sizing and a double-click reset", () => {
  const { handle } = fixture();
  fireEvent.keyDown(handle, { key: "ArrowRight" });
  expect(handle.getAttribute("aria-valuenow")).toBe("228");
  fireEvent.keyDown(handle, { key: "End" });
  fireEvent.keyDown(handle, { key: "ArrowRight" });
  expect(handle.getAttribute("aria-valuenow")).toBe("360");
  fireEvent.keyDown(handle, { key: "Home" });
  fireEvent.keyDown(handle, { key: "ArrowLeft" });
  expect(handle.getAttribute("aria-valuenow")).toBe("200");
  fireEvent.doubleClick(handle);
  expect(handle.getAttribute("aria-valuenow")).toBe("220");
  expect(localStorage.getItem(preference)).toBe("220");
});

it("restores the previous width when a drag is cancelled and ignores unrelated pointers", () => {
  const { handle } = fixture();
  pointer(handle, "pointerdown", 220);
  pointer(handle, "pointermove", 300, 2);
  expect(handle.getAttribute("aria-valuenow")).toBe("220");
  pointer(handle, "pointermove", 300);
  fireEvent.keyDown(handle, { key: "Escape" });
  pointer(handle, "pointermove", 320);
  expect(handle.getAttribute("aria-valuenow")).toBe("220");
  expect(localStorage.getItem(preference)).toBe("220");
  pointer(handle, "pointerdown", 220);
  pointer(handle, "pointermove", 300);
  pointer(handle, "pointercancel", 300);
  expect(handle.getAttribute("aria-valuenow")).toBe("220");
});

it.each(["invalid", "Infinity", "", "-200"])(
  "falls back to the default for an invalid stored width (%s)",
  (value) => {
    localStorage.setItem(preference, value);
    expect(fixture().handle.getAttribute("aria-valuenow")).toBe("220");
  },
);
