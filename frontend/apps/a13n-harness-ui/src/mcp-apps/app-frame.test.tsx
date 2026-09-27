// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { AppFrame } from "./app-frame";

const bridge = vi.hoisted(() => ({
  setHostContext: vi.fn(),
  connect: vi.fn().mockResolvedValue(undefined),
  close: vi.fn(),
  sendToolInput: vi.fn().mockResolvedValue(undefined),
  sendToolResult: vi.fn().mockResolvedValue(undefined),
  oninitialized: () => {},
}));
vi.mock("@modelcontextprotocol/ext-apps/app-bridge", () => ({
  AppBridge: class {
    constructor() {
      return bridge;
    }
  },
  PostMessageTransport: class {},
}));
beforeEach(() => {
  // jsdom does not implement HTMLIFrameElement.sandbox's DOMTokenList.
  Object.defineProperty(HTMLIFrameElement.prototype, "sandbox", {
    configurable: true,
    get: () => ({ add: vi.fn() }),
  });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
  document.documentElement.classList.remove("dark");
  document.documentElement.removeAttribute("lang");
});
const props = {
  title: "Example",
  sandboxUrl: "http://localhost:9001/sandbox.html",
  html: "<p>App</p>",
  arguments: {},
  result: { content: [] },
  capabilities: {},
  handlers: {},
};
it("updates Host theme and locale without remounting the frame or replaying tool results", async () => {
  render(<AppFrame {...props} />);
  const frame = screen.getByTitle("Example");
  await waitFor(() =>
    expect(frame.getAttribute("src")).toContain("sandbox.html"),
  );
  act(() => bridge.oninitialized());
  await waitFor(() => expect(bridge.sendToolResult).toHaveBeenCalledTimes(1));
  act(() => {
    document.documentElement.classList.add("dark");
    document.documentElement.lang = "zh-CN";
  });
  await waitFor(() =>
    expect(bridge.setHostContext).toHaveBeenLastCalledWith(
      expect.objectContaining({ theme: "dark", locale: "zh-CN" }),
    ),
  );
  expect(screen.getByTitle("Example")).toBe(frame);
  expect(bridge.connect).toHaveBeenCalledTimes(1);
  expect(bridge.sendToolResult).toHaveBeenCalledTimes(1);
});
it("keeps the original result available when sandbox startup never completes", async () => {
  vi.useFakeTimers();
  render(<AppFrame {...props} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15000);
  });
  expect(screen.getByRole("alert").textContent).toContain(
    "original tool result remains",
  );
  expect(bridge.sendToolResult).not.toHaveBeenCalled();
});
