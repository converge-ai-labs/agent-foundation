// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { createTransport } from "./client";
import { FakeWebSocket, mockWebSocket } from "../../tests/fake-websocket";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("multiplexes roots and summary, resumes independently, and rejects frames from replaced channels", async () => {
  vi.useFakeTimers();
  const socket = mockWebSocket();
  const transport = createTransport("test-only-key", vi.fn());
  const summary = vi.fn(),
    focus = vi.fn();
  let cursor = "root-before";
  const a = transport.realtime.subscribe({
    stream: "summary",
    cursor: () => "summary-before",
    receive: summary,
    state: vi.fn(),
  });
  const b = transport.realtime.subscribe({
    stream: "focus",
    root: "one",
    cursor: () => cursor,
    receive: focus,
    state: vi.fn(),
  });
  expect(FakeWebSocket.instances).toHaveLength(1);
  socket().open();
  expect(socket().url.toString()).not.toContain("test-only-key");
  expect(socket().sent[0]).toEqual({ api_key: "test-only-key" });
  const ids = socket()
    .sent.slice(1)
    .map((item) => item.channel);
  socket().frame({ kind: "open" }, ids[0]);
  socket().frame({ kind: "event" }, ids[1]);
  expect(summary).toHaveBeenCalledOnce();
  expect(focus).toHaveBeenCalledOnce();
  cursor = "root-after";
  b.restart();
  socket().frame({ kind: "obsolete" }, ids[1]);
  expect(focus).toHaveBeenCalledOnce();
  expect(FakeWebSocket.instances).toHaveLength(1);
  socket().close();
  await vi.advanceTimersByTimeAsync(1000);
  socket().open();
  expect(
    socket()
      .sent.slice(1)
      .map((item) => item.after),
  ).toEqual(["summary-before", "root-after"]);
  socket().message({ version: 1, kind: "ping" });
  expect(socket().sent.at(-1)?.kind).toBe("pong");
  a();
  expect(socket().readyState).toBe(1);
  b();
  expect(socket().readyState).toBe(3);
  transport.close();
});

it("terminates the entire access lifetime on rejected socket authentication", async () => {
  const socket = mockWebSocket();
  const rejected = vi.fn();
  const transport = createTransport("invalid", rejected);
  transport.realtime.subscribe({
    stream: "summary",
    cursor: () => undefined,
    receive: vi.fn(),
    state: vi.fn(),
  });
  socket().open();
  socket().close(4401);
  expect(rejected).toHaveBeenCalledOnce();
  await expect(transport.fetch("/api/status")).rejects.toMatchObject({
    name: "AbortError",
  });
  transport.close();
});
