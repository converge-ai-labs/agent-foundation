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
  expect(session.state.message).toContain("Some output is missing");
  session.dispose();
});

it("bounds unparsed output and resumes from the parsed cursor without silently dropping a gap", async () => {
  const { session, emit, callbacks, socket } = setup();
  emit(frame(new Uint8Array(OUTPUT_LIMIT)));
  emit(frame(new Uint8Array([65]), OUTPUT_LIMIT));
  expect(socket.close).toHaveBeenCalledTimes(1);
  expect(session.state.message).toContain("view fell behind");
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
  expect(session.state.message).toContain("Couldn't confirm");
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
  expect(session.state.message).toContain("Some input may have arrived");
  session.dispose();
});

it("coalesces split TUI redraw sequences, drains queued batches without extra timer stalls, and skips redundant state notifications on pure output", async () => {
  const { session, emit, callbacks, text } = setup();
  const changed = vi.spyOn(
    session as unknown as { changed: () => void },
    "changed",
  );
  // First frame sets up Live state.
  emit(frame(new Uint8Array(), 0, 1, "participant-self"));
  changed.mockClear();

  // Split a DEC 2026 synchronized redraw across two frames arriving 6ms apart.
  const part1 = new TextEncoder().encode("\x1b[?2026h\x1b[2J\x1b[Hclearing...");
  const part2 = new TextEncoder().encode("redrawn\x1b[?2026l");
  emit(frame(part1, 0, 1, "participant-self"));
  // Pure output frame with unchanged view metadata must not trigger React re-renders.
  expect(changed).not.toHaveBeenCalled();

  await vi.advanceTimersByTimeAsync(6);
  expect(callbacks).toHaveLength(0);
  emit(frame(part2, part1.length, 1, "participant-self"));
  // At 8ms the incomplete-redraw grace check ran; by 16ms the combined frame flushes as one atomic write.
  await vi.advanceTimersByTimeAsync(10);
  expect(callbacks).toHaveLength(1);
  expect(text()).toBe("\x1b[?2026h\x1b[2J\x1b[Hclearing...redrawn\x1b[?2026l");

  // While the first write is still in progress, queue a complete frame and verify it drains immediately on callback.
  const offset = part1.length + part2.length;
  const nextChunk = new TextEncoder().encode("immediate-drain\r\n");
  emit(frame(nextChunk, offset, 1, "participant-self"));
  expect(callbacks).toHaveLength(1);
  callbacks.shift()!();
  expect(callbacks).toHaveLength(1);
  expect(text()).toContain("immediate-drain\r\n");
  callbacks.shift()!();
  expect(session.cursor).toBe(offset + nextChunk.length);
  expect(changed).not.toHaveBeenCalled();
  session.dispose();
});

it("preserves control across view suspend and unexpected socket drops when no other participant took over, and batches input under transport backpressure", async () => {
  const { session, emit, socket } = setup();
  emit(frame(new Uint8Array(), 0, 1, "participant-self"));
  expect(session.controls).toBe(true);

  // Under moderate socket backpressure (0 < bufferedAmount <= 64 KiB), rapid inputs coalesce into one frame.
  socket.send.mockClear();
  socket.bufferedAmount = 1024;
  expect(session.input("a")).toBe(true);
  expect(session.input("b")).toBe(true);
  expect(socket.send).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(8);
  expect(socket.send).toHaveBeenCalledTimes(1);
  expect(JSON.parse(socket.send.mock.calls[0][0])).toEqual({
    kind: "input",
    text: "ab",
    control_epoch: 1,
  });

  // Suspending the view (e.g. switching tabs/windows) preserves control intent and reclaims once if unowned.
  session.suspend();
  expect(session.recoverable).toBe(true);
  session.connect("https://example.invalid", "secret");
  const second = Socket.all[1];
  second.onopen();
  second.send.mockClear();
  emit.call(null, frame());
  second.onmessage({
    data: JSON.stringify(frame(new Uint8Array(), 0, 2, null)),
  });
  expect(second.send).toHaveBeenCalledTimes(1);
  expect(JSON.parse(second.send.mock.calls[0][0])).toEqual({
    kind: "control",
    control_epoch: 2,
    release: false,
  });
  second.onmessage({
    data: JSON.stringify(frame(new Uint8Array(), 0, 3, "participant-self")),
  });
  expect(session.controls).toBe(true);

  // An unexpected WebSocket drop also marks the session recoverable, but does not steal control if another user took over.
  second.onclose({ code: 1006 });
  expect(session.recoverable).toBe(true);
  session.connect("https://example.invalid", "secret");
  const third = Socket.all[2];
  third.onopen();
  third.send.mockClear();
  third.onmessage({
    data: JSON.stringify(frame(new Uint8Array(), 0, 4, "participant-other")),
  });
  expect(third.send).not.toHaveBeenCalled();
  expect(session.controls).toBe(false);
  session.dispose();
});
