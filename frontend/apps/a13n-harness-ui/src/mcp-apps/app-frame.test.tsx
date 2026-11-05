// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { LanguageChoice, LanguageProvider } from "../i18n/language";
import { i18n } from "../i18n";
import userEvent from "@testing-library/user-event";
import { AppFrame } from "./app-frame";

const bridge = vi.hoisted(() => ({
  construct: vi.fn(),
  setHostContext: vi.fn(),
  connect: vi.fn().mockResolvedValue(undefined),
  close: vi.fn(),
  sendToolInput: vi.fn().mockResolvedValue(undefined),
  sendToolResult: vi.fn().mockResolvedValue(undefined),
  oninitialized: () => {},
}));
vi.mock("@modelcontextprotocol/ext-apps/app-bridge", () => ({
  AppBridge: class {
    constructor(...args: unknown[]) {
      bridge.construct(...args);
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
afterEach(async () => {
  cleanup();
  localStorage.clear();
  await i18n.changeLanguage("en");
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
it("supplies initial context without notifying an uninitialized App", async () => {
  document.documentElement.lang = "en-US";
  render(<AppFrame {...props} />);
  expect(bridge.construct).toHaveBeenCalledWith(
    null,
    expect.any(Object),
    props.capabilities,
    {
      hostContext: expect.objectContaining({ theme: "light", locale: "en-US" }),
    },
  );
  await act(async () => {
    document.documentElement.classList.add("dark");
    window.dispatchEvent(new Event("languagechange"));
  });
  expect(bridge.setHostContext).not.toHaveBeenCalled();
  act(() => bridge.oninitialized());
  expect(bridge.setHostContext).toHaveBeenLastCalledWith(
    expect.objectContaining({ theme: "dark", locale: "en-US" }),
  );
  await waitFor(() => expect(bridge.sendToolResult).toHaveBeenCalledTimes(1));
  cleanup();
  bridge.setHostContext.mockClear();
  await act(async () => {
    document.documentElement.lang = "fr";
    window.dispatchEvent(new Event("languagechange"));
  });
  expect(bridge.setHostContext).not.toHaveBeenCalled();
});
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
    "saved tool result stays",
  );
  expect(bridge.sendToolResult).not.toHaveBeenCalled();
});

it("propagates the actual display-language control to an initialized MCP App without reconnecting or replaying results", async () => {
  render(
    <LanguageProvider>
      <LanguageChoice />
      <AppFrame {...props} />
    </LanguageProvider>,
  );
  const frame = screen.getByTitle("Example");
  act(() => bridge.oninitialized());
  await waitFor(() => expect(bridge.sendToolResult).toHaveBeenCalledTimes(1));
  await userEvent.click(
    screen.getByRole("combobox", { name: "Display language" }),
  );
  await userEvent.click(
    await screen.findByRole("option", { name: "简体中文" }),
  );
  await waitFor(() =>
    expect(bridge.setHostContext).toHaveBeenLastCalledWith(
      expect.objectContaining({ locale: "zh-CN" }),
    ),
  );
  expect(screen.getByTitle("Example")).toBe(frame);
  expect(bridge.connect).toHaveBeenCalledTimes(1);
  expect(bridge.sendToolResult).toHaveBeenCalledTimes(1);
});

it("retranslates local sandbox failures without reconnecting or translating external errors", async () => {
  const view = render(
    <AppFrame {...props} sandboxUrl={`${window.location.origin}/sandbox`} />,
  );
  expect(screen.getByRole("alert").textContent).toContain(
    "MCP Apps require a separate sandbox origin.",
  );
  await act(async () => {
    await i18n.changeLanguage("zh-CN");
  });
  expect(screen.getByRole("alert").textContent).toContain(
    "MCP 应用需要独立的沙箱源",
  );
  expect(bridge.connect).not.toHaveBeenCalled();
  view.unmount();
  bridge.connect.mockRejectedValueOnce(
    new Error("MCP Apps require a separate sandbox origin."),
  );
  render(<AppFrame {...props} />);
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toContain(
      "MCP Apps require a separate sandbox origin.",
    ),
  );
  expect(bridge.connect).toHaveBeenCalledTimes(1);
});
