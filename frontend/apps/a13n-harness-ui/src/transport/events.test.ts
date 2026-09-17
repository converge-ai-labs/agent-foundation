// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { summaryFrame } from "./events";
import { ApiError, createTransport, result } from "./client";
import { mockWebSocket, FakeWebSocket } from "../../tests/fake-websocket";

afterEach(() => vi.unstubAllGlobals());
it("rejects malformed summary envelopes instead of inventing cursors", () => {
  expect(() =>
    summaryFrame({ kind: "invalidation", resume_cursor: 3 }),
  ).toThrow("Invalid summary frame");
  expect(summaryFrame({ kind: "reset", reason: "epoch_changed" })).toEqual({
    kind: "reset",
    reason: "epoch_changed",
  });
});
it("uses header auth for typed calls and propagates bounded API errors", async () => {
  const expired = vi.fn();
  const fetcher = vi.fn(async (request: Request) => {
    expect(request.headers.get("Authorization")).toBe("Bearer example-key");
    expect(request.url).not.toContain("example-key");
    return new Response(
      JSON.stringify({
        error: {
          code: "configuration_invalid",
          message: "Candidate rejected.",
        },
      }),
      { status: 400 },
    );
  });
  vi.stubGlobal("fetch", fetcher);
  const transport = createTransport("example-key", expired);
  await expect(
    result(
      transport.client.POST("/api/configuration/validate", {
        params: { query: { path: "agents/test.yaml" } },
        body: { content: "bad" },
      }),
    ),
  ).rejects.toMatchObject({
    message: "Candidate rejected.",
    code: "configuration_invalid",
    status: 400,
  });
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(expired).not.toHaveBeenCalled();
});
it("announces unauthorized responses and never retries the write", async () => {
  const expired = vi.fn();
  const fetcher = vi.fn().mockResolvedValue(new Response("", { status: 401 }));
  vi.stubGlobal("fetch", fetcher);
  const transport = createTransport("example-key", expired);
  const request = result(
    transport.client.PUT("/api/auth/keys", {
      body: { credential_ref: "key-one", key: "provider-secret" },
    }),
  );
  await expect(request).rejects.toBeInstanceOf(ApiError);
  await expect(request).rejects.toMatchObject({ status: 401 });
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(expired).toHaveBeenCalledOnce();
  transport.close();
});

it("closes pending writes on rejected access and ignores their late success", async () => {
  let release!: (response: Response) => void;
  let writeSignal: AbortSignal | undefined;
  const expired = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) => {
      if (request.method === "POST") {
        writeSignal = request.signal;
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      }
      return Promise.resolve(new Response("", { status: 401 }));
    }),
  );
  const transport = createTransport("example-key", expired);
  const received = vi.fn();
  const pending = result(
    transport.client.POST("/api/auth/logins", { body: { provider: "codex" } }),
  ).then(received, (error: unknown) => error);
  await expect(
    result(transport.client.GET("/api/status")),
  ).rejects.toMatchObject({ status: 401 });
  expect(writeSignal?.aborted).toBe(true);
  release(new Response(JSON.stringify({ session_id: "old-login" })));
  await pending;
  expect(received).not.toHaveBeenCalled();
  await expect(
    result(transport.client.GET("/api/status")),
  ).rejects.toMatchObject({ name: "AbortError" });
});

it("rejoins summary streams with cursors, discards reset cursors and cancels retries", async () => {
  vi.useFakeTimers();
  try {
    const socket = mockWebSocket();
    const transport = createTransport("example-key", vi.fn());
    const invalidate = vi.fn();
    const states = vi.fn();
    const { watchSummary } = await import("./events");
    const close = watchSummary(transport, invalidate, states);
    socket().open();
    socket().frame({ kind: "open", resume_cursor: "epoch:7", resumed: false });
    expect(invalidate).toHaveBeenCalledOnce();
    expect(states).toHaveBeenCalledWith("Live");
    socket().close();
    await vi.advanceTimersByTimeAsync(1000);
    socket().open();
    expect(socket().sent.at(-1)?.after).toBe("epoch:7");
    socket().frame({ kind: "open", resume_cursor: "epoch:7", resumed: true });
    expect(invalidate).toHaveBeenCalledOnce();
    socket().frame({ kind: "reset", reason: "epoch_changed" });
    await vi.advanceTimersByTimeAsync(1000);
    expect(socket().sent.at(-1)?.after).toBeNull();
    expect(FakeWebSocket.instances).toHaveLength(2);
    close();
    await vi.advanceTimersByTimeAsync(30000);
    expect(FakeWebSocket.instances).toHaveLength(2);
    transport.close();
  } finally {
    vi.useRealTimers();
  }
});

it("retains root identity and bounded notices rather than reducing every event to a global refresh", async () => {
  const event = {
    kind: "root_operation",
    epoch: "epoch",
    sequence: 2,
    root_thread_id: "thread-1",
    notice: {
      receipt_id: "receipt-1",
      status: "completed",
      brief: "Fixed the sidebar.",
    },
  };
  const envelope = { kind: "invalidation", resume_cursor: "epoch:2", event };
  expect(summaryFrame(envelope)).toEqual(envelope);
  for (const notice of [
    { ...event.notice, status: "running" },
    { ...event.notice, brief: "x".repeat(321) },
    { ...event.notice, receipt_id: null },
  ]) {
    expect(() =>
      summaryFrame({ ...envelope, event: { ...event, notice } }),
    ).toThrow("Invalid root operation notice");
  }
  const socket = mockWebSocket();
  const { watchSummary } = await import("./events");
  const received = vi.fn();
  const transport = createTransport("test", () => {});
  const stop = watchSummary(transport, received, () => {});
  try {
    socket().open();
    socket().frame(envelope);
    expect(received).toHaveBeenCalledWith(event);
  } finally {
    stop();
    transport.close();
  }
});

it("manual retry retains the summary cursor and receives missed notices without overlapping requests", async () => {
  vi.useFakeTimers();
  const notice = {
    kind: "root_operation",
    epoch: "epoch",
    sequence: 8,
    root_thread_id: "thread-1",
    notice: {
      receipt_id: "receipt-1",
      status: "completed",
      brief: "Finished while disconnected.",
    },
  };
  const socket = mockWebSocket();
  const { watchSummary } = await import("./events");
  const transport = createTransport("", vi.fn());
  const receive = vi.fn();
  const close = watchSummary(transport, receive, vi.fn());
  try {
    socket().open();
    socket().frame({ kind: "open", resume_cursor: "epoch:7", resumed: false });
    socket().close();
    close.retry();
    close.retry();
    socket().open();
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(socket().sent.at(-1)?.after).toBe("epoch:7");
    socket().frame({
      kind: "invalidation",
      resume_cursor: "epoch:8",
      event: notice,
    });
    expect(receive).toHaveBeenCalledWith(notice);
    close();
    close.retry();
    await vi.advanceTimersByTimeAsync(30000);
    expect(FakeWebSocket.instances).toHaveLength(2);
  } finally {
    close();
    transport.close();
    vi.useRealTimers();
  }
});
