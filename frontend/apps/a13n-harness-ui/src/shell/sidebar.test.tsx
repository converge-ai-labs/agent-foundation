// @vitest-environment jsdom
import { i18n } from "../i18n";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useState } from "react";
import { WorkbenchSidebar, MobileNavigation } from "./sidebar";

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
afterEach(async () => {
  cleanup();
  await i18n.changeLanguage("en");
  vi.unstubAllGlobals();
});

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

it("keeps an active resize and its width when the display language changes", async () => {
  const { handle } = fixture();
  pointer(handle, "pointerdown", 220);
  pointer(handle, "pointermove", 280);
  await act(() => i18n.changeLanguage("zh-CN"));
  expect(screen.getByRole("separator", { name: "调整导航宽度" })).toBe(handle);
  expect(handle.getAttribute("aria-valuetext")).toBe("280 像素");
  expect(localStorage.getItem(preference)).toBe("220");
  pointer(handle, "pointermove", 300);
  pointer(handle, "pointerup", 300);
  expect(localStorage.getItem(preference)).toBe("300");
  expect(handle.releasePointerCapture).toHaveBeenCalledWith(1);
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

function touch(target: HTMLElement, type: string, x: number, y = 100) {
  const event = new Event(type, { bubbles: true, cancelable: true });
  Object.defineProperty(event, "touches", {
    value: type === "touchend" ? [] : [{ clientX: x, clientY: y }],
  });
  fireEvent(target, event);
}

it("opens from the edge, follows the finger and closes with a directional swipe", () => {
  viewport(390);
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  function Drawer() {
    const [open, setOpen] = useState(false);
    return (
      <MobileNavigation
        open={open}
        onOpenChange={setOpen}
        finalFocus={() => null}
      >
        Navigation
      </MobileNavigation>
    );
  }
  render(<Drawer />);
  touch(document.body, "touchstart", 2);
  touch(document.body, "touchmove", 202);
  const dialog = screen.getByRole("dialog", { name: "Harness" });
  expect(dialog.style.transform).toBe("translateX(-100px)");
  touch(document.body, "touchend", 202);
  expect(dialog.style.transform).toBe("");
  touch(dialog, "touchstart", 250);
  touch(dialog, "touchmove", 20);
  touch(dialog, "touchend", 20);
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("leaves vertical gestures and nested horizontal readers to native scrolling", () => {
  viewport(390);
  const change = vi.fn();
  render(
    <MobileNavigation
      open={false}
      onOpenChange={change}
      finalFocus={() => null}
    >
      Navigation
    </MobileNavigation>,
  );
  touch(document.body, "touchstart", 2);
  touch(document.body, "touchmove", 4, 140);
  touch(document.body, "touchend", 4);
  expect(change).not.toHaveBeenCalled();
  const reader = document.createElement("div");
  reader.style.overflowX = "auto";
  Object.defineProperties(reader, {
    scrollWidth: { value: 800 },
    clientWidth: { value: 300 },
  });
  document.body.append(reader);
  touch(reader, "touchstart", 2);
  touch(reader, "touchmove", 202);
  touch(reader, "touchend", 202);
  expect(change).not.toHaveBeenCalled();
  reader.remove();
});
