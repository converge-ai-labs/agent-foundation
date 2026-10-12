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
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
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
      <VoiceIndicator />
    </>
  );
}
function mount(enabled = true) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ enabled }), {
          headers: { "Content-Type": "application/json" },
        }),
    ),
  );
  const transport = createTransport("test", vi.fn());
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <TransportContext.Provider value={transport}>
        <MemoryRouter initialEntries={["/threads/thread-example"]}>
          <VoiceProvider>
            <Routes>
              <Route path="/threads/:id" element={<Workspace />} />
              <Route path="/live" element={<VoicePage />} />
            </Routes>
          </VoiceProvider>
        </MemoryRouter>
      </TransportContext.Provider>
    </QueryClientProvider>,
  );
}

it("starts voice in place, retains the call on navigation, and hides pending captions after stop", async () => {
  mount();
  const start = await screen.findByRole("button", { name: "Start voice" });
  await waitFor(() =>
    expect((start as HTMLButtonElement).disabled).toBe(false),
  );
  expect(voice.start).not.toHaveBeenCalled();
  fireEvent.click(start);
  expect(voice.start).toHaveBeenCalledTimes(1);
  expect(screen.getByText("/threads/thread-example")).toBeTruthy();
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
  fireEvent.click(screen.getByRole("link", { name: "Open Live conversation" }));
  expect(
    await screen.findByRole("heading", { name: "Live conversation" }),
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
  const start = await screen.findByRole("button", { name: "Start voice" });
  await waitFor(() =>
    expect((start as HTMLButtonElement).disabled).toBe(false),
  );
  fireEvent.click(start);
  act(() =>
    publish({ status: "failed", error: "Microphone permission denied" }),
  );
  expect(screen.getByRole("alert").textContent).toBe(
    "Microphone permission denied",
  );
  expect(voice.start).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Start voice" }));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(voice.start).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Stop voice" }));
  expect(voice.stop).toHaveBeenCalledTimes(1);
  expect(screen.getByText("/threads/thread-example")).toBeTruthy();
});

it("offers settings instead of microphone capture when Live is not configured", async () => {
  mount(false);
  const setup = await screen.findByRole("link", { name: "Set up voice" });
  expect(setup.getAttribute("href")).toBe("/settings#live");
  expect(screen.queryByRole("button", { name: "Start voice" })).toBeNull();
  expect(voice.start).not.toHaveBeenCalled();
});
