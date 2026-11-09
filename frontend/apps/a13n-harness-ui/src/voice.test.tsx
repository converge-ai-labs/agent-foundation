// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { i18n } from "./i18n";
import { createTransport } from "./transport/client";
import { TransportContext } from "./transport/context";
import { VoiceIndicator, VoicePage, VoiceProvider } from "./voice";
import type { VoiceState } from "./voice-session";

const voice = vi.hoisted(() => ({
  state: { status: "idle", error: "", snapshot: null } as VoiceState,
  listeners: new Set<() => void>(),
  start: vi.fn(),
  stop: vi.fn(),
}));
vi.mock("./voice-session", () => ({
  VoiceSession: class {
    start = voice.start;
    stop = voice.stop;
    getSnapshot = () => voice.state;
    subscribe = (listener: () => void) => {
      voice.listeners.add(listener);
      return () => voice.listeners.delete(listener);
    };
  },
}));
function publish(patch: Partial<VoiceState>) {
  voice.state = { ...voice.state, ...patch };
  voice.listeners.forEach((listener) => listener());
}
beforeEach(() => {
  voice.state = { status: "idle", error: "", snapshot: null };
  voice.listeners.clear();
  voice.start
    .mockReset()
    .mockImplementation(async () => publish({ status: "starting", error: "" }));
  voice.stop
    .mockReset()
    .mockImplementation(() => publish({ status: "idle", error: "" }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function Workspace() {
  const location = useLocation();
  return (
    <>
      <h1>Workspace</h1>
      <p>{location.pathname}</p>
      <Link to="/live">Live</Link>
      <VoiceIndicator />
    </>
  );
}
function mount(enabled = true, response?: () => Promise<Response>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      response ??
        (async () =>
          new Response(JSON.stringify({ enabled }), {
            headers: { "Content-Type": "application/json" },
          })),
    ),
  );
  const transport = createTransport("test", vi.fn());
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <TransportContext.Provider value={transport}>
        <MemoryRouter initialEntries={["/threads/thread-example"]}>
          <VoiceProvider>
            <Routes>
              <Route path="/" element={<Workspace />} />
              <Route path="/threads/:id" element={<Workspace />} />
              <Route path="/live" element={<VoicePage />} />
            </Routes>
          </VoiceProvider>
        </MemoryRouter>
      </TransportContext.Provider>
    </QueryClientProvider>,
  );
  return client;
}

async function startFromLivePage() {
  fireEvent.click(screen.getByRole("link", { name: "Live" }));
  const start = await screen.findByRole("button", { name: "Start voice" });
  await waitFor(() => expect(start.hasAttribute("disabled")).toBe(false));
  expect(voice.start).not.toHaveBeenCalled();
  fireEvent.click(start);
  fireEvent.click(screen.getByRole("link", { name: "Workspace" }));
}

it("starts voice on the Live page, retains the call on navigation, and hides pending captions after stop", async () => {
  mount();
  await startFromLivePage();
  expect(voice.start).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Stop voice" })).toBeTruthy();
  act(() =>
    publish({
      status: "active",
      snapshot: {
        status: "active",
        enabled: true,
        user_text: "My question",
        assistant_text: "My answer",
        transcript: [],
      },
    }),
  );
  fireEvent.click(screen.getByRole("link", { name: "Open Live chat" }));
  expect(
    await screen.findByRole("heading", { name: "Live chat" }),
  ).toBeTruthy();
  expect(screen.getByRole("status").textContent).toBe("Connected");
  expect(screen.getByText("You")).toBeTruthy();
  expect(screen.getByText("Assistant")).toBeTruthy();
  expect(screen.getByText("My question")).toBeTruthy();
  expect(screen.getByText("My answer")).toBeTruthy();
  expect(voice.start).toHaveBeenCalledTimes(1);
  expect(voice.stop).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Stop voice" }));
  expect(voice.stop).toHaveBeenCalledTimes(1);
  expect(screen.queryByText("My question")).toBeNull();
  expect(screen.queryByText("My answer")).toBeNull();
});

it("shows workspace connection errors and allows explicit retry or startup cancellation", async () => {
  mount();
  await startFromLivePage();
  act(() =>
    publish({ status: "failed", error: "Microphone permission denied" }),
  );
  expect(screen.getByRole("alert").textContent).toBe(
    "Microphone permission denied",
  );
  expect(voice.start).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Retry voice" }));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(voice.start).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Stop voice" }));
  expect(voice.stop).toHaveBeenCalledTimes(1);
});

it("switches retained local voice errors while preserving provider messages", async () => {
  mount();
  act(() =>
    publish({
      status: "failed",
      error: "Microphone access ended.",
      errorLocalizable: true,
    }),
  );
  try {
    await act(() => i18n.changeLanguage("zh-CN"));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "麦克风访问已结束。",
    );
    act(() =>
      publish({ error: "Microphone access ended.", errorLocalizable: false }),
    );
    expect(screen.getByRole("alert").textContent).toBe(
      "Microphone access ended.",
    );
  } finally {
    await act(() => i18n.changeLanguage("en"));
  }
});

it("keeps idle voice out of the header before and after settings arrive", async () => {
  let resolve!: (response: Response) => void;
  const client = mount(
    true,
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  expect(screen.queryByRole("button", { name: "Start voice" })).toBeNull();
  expect(screen.queryByRole("link", { name: "Set up voice" })).toBeNull();
  await waitFor(() => expect(resolve).toBeTypeOf("function"));
  resolve(
    new Response(JSON.stringify({ enabled: true }), {
      headers: { "Content-Type": "application/json" },
    }),
  );
  await waitFor(() => expect(client.isFetching()).toBe(0));
  expect(screen.queryByRole("button", { name: "Start voice" })).toBeNull();
  expect(voice.start).not.toHaveBeenCalled();
});

it.each(["disabled", "unavailable"])(
  "hides workspace voice controls when settings are %s",
  async (state) => {
    const client = mount(
      false,
      state === "unavailable"
        ? async () => {
            throw new TypeError("Failed to fetch");
          }
        : undefined,
    );
    await waitFor(() => expect(client.isFetching()).toBe(0));
    expect(client.getQueryState(["voice-status"])?.status).toBe(
      state === "disabled" ? "success" : "error",
    );
    expect(screen.queryByRole("button", { name: "Start voice" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Set up voice" })).toBeNull();
    expect(voice.start).not.toHaveBeenCalled();
  },
);

it.each(["starting", "connecting", "active"] as const)(
  "retains Stop when Live is disabled during a %s call",
  async (status) => {
    const client = mount();
    act(() => {
      publish({ status });
      client.setQueryData(["voice-status"], { enabled: false });
    });
    await waitFor(() =>
      expect(client.getQueryData(["voice-status"])).toEqual({ enabled: false }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Stop voice" }));
    expect(voice.stop).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Start voice" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Open Live chat" })).toBeNull();
  },
);

it("changes language during a call without restarting it or changing captions", async () => {
  mount();
  await startFromLivePage();
  act(() =>
    publish({
      status: "active",
      snapshot: {
        status: "active",
        enabled: true,
        user_text: "Original caption",
        transcript: [],
      },
    }),
  );
  fireEvent.click(screen.getByRole("link", { name: "Open Live chat" }));
  await act(async () => {
    await i18n.changeLanguage("zh-CN");
  });
  expect(screen.getByRole("button", { name: "停止语音" })).toBeTruthy();
  expect(screen.getByText("Original caption")).toBeTruthy();
  expect(voice.start).toHaveBeenCalledTimes(1);
  expect(voice.stop).not.toHaveBeenCalled();
  await act(async () => {
    await i18n.changeLanguage("en");
  });
});

it.each([
  ["client fallback", "", "请求失败（500）。"],
  [
    "server message",
    JSON.stringify({ error: { message: "Request failed (500)." } }),
    "Request failed (500).",
  ],
])(
  "localizes voice settings %s by origin in both views",
  async (_kind, body, expected) => {
    voice.state = { status: "active", error: "", snapshot: null };
    mount(true, async () => new Response(body, { status: 500 }));
    try {
      expect((await screen.findByRole("alert")).textContent).toBe(
        "Request failed (500).",
      );
      await act(() => i18n.changeLanguage("zh-CN"));
      expect(screen.getByRole("alert").textContent).toBe(expected);
      fireEvent.click(screen.getByRole("link", { name: "打开语音聊天" }));
      expect((await screen.findByRole("alert")).textContent).toBe(expected);
      expect(voice.stop).not.toHaveBeenCalled();
    } finally {
      await act(() => i18n.changeLanguage("en"));
    }
  },
);
