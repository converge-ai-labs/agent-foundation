import { afterEach, expect, it, vi } from "vitest";
import { OUTPUT_LIMIT, TerminalConnection } from "./terminal-connection";
import type { Schema } from "../transport/client";

class Socket {
  static OPEN = 1;
  static all: Socket[] = [];
  readyState = 1;
  bufferedAmount = 0;
  onopen = () => {};
  onmessage = (_: { data: string }) => {};
  onclose = (_: { code: number }) => {};
  send = vi.fn();
  close = vi.fn();
  constructor(readonly url: URL) {
    Socket.all.push(this);
  }
}
function frame(
  bytes: Uint8Array = new Uint8Array(),
  start = 0,
  epoch = 0,
  controller: string | null = null,
): Schema<"TerminalFrame"> {
  return {
    kind: "terminal",
    participant_id: "participant-self",
    terminal: {
      terminal_id: "terminal-one",
      cwd: "/tmp",
      project_id: null,
      shell: "/bin/sh",
      state: "running",
      exit_code: null,
      controller,
      control_epoch: epoch,
      participants: ["participant-self"],
      output_start: 0,
      output_end: start + bytes.length,
    },
    start,
    end: start + bytes.length,
    gap: false,
    data_base64: Buffer.from(bytes).toString("base64"),
  };
}
function setup() {
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", Socket);
  Socket.all = [];
  let text = "";
  const callbacks: (() => void)[] = [];
  const reset = vi.fn();
  const unauthorized = vi.fn();
  const session = new TerminalConnection(
    "terminal-one",
    {
      write: (value, done) => {
        text += value;
        callbacks.push(done);
      },
      reset,
    },
    vi.fn(),
    unauthorized,
  );
  session.connect("https://example.invalid", "secret");
  const socket = Socket.all[0];
  const emit = (value: Schema<"TerminalFrame">) =>
    socket.onmessage({ data: JSON.stringify(value) });
  return {
    session,
    socket,
    emit,
    reset,
    callbacks,
    unauthorized,
    text: () => text,
  };
}
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("authenticates in the first frame and waits for confirmed authority; reconnect never replays input or takeover", () => {
  const { session, socket, emit } = setup();
  expect(socket.url.search).toBe("?cursor=0");
  expect(socket.url.href).not.toContain("secret");
  socket.onopen();
  expect(JSON.parse(socket.send.mock.calls[0][0])).toEqual({
    api_key: "secret",
  });
  emit(frame());
  expect(session.input("before")).toBe(false);
  session.control();
  expect(session.controls).toBe(false);
  expect(session.input("pending")).toBe(false);
  emit(frame(new Uint8Array(), 0, 1, "participant-self"));
  expect(session.controls).toBe(true);
  session.input("echo once\r");
  session.resize(32, 100);
  expect(JSON.parse(socket.send.mock.calls.at(-1)![0])).toEqual({
    kind: "resize",
    control_epoch: 1,
    rows: 32,
    columns: 100,
  });
  emit(frame(new Uint8Array(), 0, 2, "participant-other"));
  expect(session.input("stale")).toBe(false);
  session.detach();
  session.connect("https://example.invalid", "secret");
  expect(Socket.all[1].send).not.toHaveBeenCalled();
  Socket.all[1].onopen();
  expect(Socket.all[1].send).toHaveBeenCalledTimes(1);
  expect(session.controls).toBe(false);
  session.dispose();
});

it("decodes split UTF-8, batches output and advances only after parser completion; gaps reset decoding", async () => {
  const { session, emit, callbacks, text, reset } = setup();
  const bytes = new TextEncoder().encode("界");
  emit(frame(bytes.slice(0, 1)));
  emit(frame(bytes.slice(1), 1));
  await vi.advanceTimersByTimeAsync(16);
  expect(text()).toBe("界");
  expect(callbacks).toHaveLength(1);
  expect(session.cursor).toBe(0);
  callbacks.shift()!();
  expect(session.cursor).toBe(3);
  emit({ ...frame(bytes.slice(0, 1), 3), gap: false });
  await vi.advanceTimersByTimeAsync(16);
  callbacks.shift()!();
  emit({ ...frame(new TextEncoder().encode("fresh"), 500), gap: true });
  await vi.advanceTimersByTimeAsync(16);
  expect(reset).toHaveBeenCalledTimes(1);
  expect(text()).toBe("界fresh");
  callbacks.shift()!();
  expect(session.cursor).toBe(505);
  expect(session.state.message).toContain("Output gap");
  session.dispose();
});

it("bounds unparsed output and resumes from the parsed cursor without silently dropping a gap", async () => {
  const { session, emit, callbacks, socket } = setup();
  emit(frame(new Uint8Array(OUTPUT_LIMIT)));
  emit(frame(new Uint8Array([65]), OUTPUT_LIMIT));
  expect(socket.close).toHaveBeenCalledTimes(1);
  expect(session.state.message).toContain("could not keep up");
  session.connect("https://example.invalid", "secret");
  expect(Socket.all).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(16);
  callbacks.shift()!();
  session.connect("https://example.invalid", "secret");
  expect(Socket.all[1].url.searchParams.get("cursor")).toBe(
    String(OUTPUT_LIMIT),
  );
  expect(session.controls).toBe(false);
  session.dispose();
});

it("uncertain control, rejected commands, congestion and auth failures remove authority without retries", async () => {
  const { session, socket, emit, unauthorized } = setup();
  emit(frame());
  session.control();
  await vi.advanceTimersByTimeAsync(10000);
  expect(session.state.message).toContain("uncertain");
  expect(socket.send).toHaveBeenCalledTimes(1);
  session.connect("https://example.invalid", "secret");
  Socket.all[1].onclose({ code: 4401 });
  expect(unauthorized).toHaveBeenCalledTimes(1);
  session.dispose();
});

it("rejects oversized paste without sending a prefix and disconnects a congested input transport", () => {
  const { session, emit, socket } = setup();
  emit(frame(new Uint8Array(), 0, 1, "participant-self"));
  expect(session.input("x".repeat(16385))).toBe(false);
  expect(socket.send).not.toHaveBeenCalled();
  socket.bufferedAmount = 65537;
  expect(session.input("must not queue")).toBe(false);
  expect(session.controls).toBe(false);
  expect(session.state.message).toContain("partial");
  session.dispose();
});
