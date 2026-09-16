// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { consumeSse, summaryFrame } from "./events";
import { ApiError, createTransport, result } from "./client";

afterEach(() => vi.unstubAllGlobals());
it("decodes split UTF-8, CRLF, comments and multiple data lines", async () => {
  const bytes = new TextEncoder().encode(
    ': heartbeat\r\ndata: {"message":\r\ndata: "中文"}\r\n\r\ndata: {"second":true}\n\n',
  );
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
      controller.close();
    },
  });
  const frames: unknown[] = [];
  await consumeSse(new Response(stream), (frame) => frames.push(frame));
  expect(frames).toEqual([{ message: "中文" }, { second: true }]);
});
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
    const requests: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        requests.push(request.url);
        return new Response(
          requests.length === 1
            ? 'data: {"kind":"open","resume_cursor":"epoch:7"}\n\n'
            : 'data: {"kind":"reset","reason":"epoch_changed"}\n\n',
        );
      }),
    );
    const transport = createTransport("example-key", vi.fn());
    const invalidate = vi.fn();
    const states = vi.fn();
    const { watchSummary } = await import("./events");
    const close = watchSummary(transport, invalidate, states);
    await vi.advanceTimersByTimeAsync(0);
    expect(invalidate).toHaveBeenCalledOnce();
    expect(states).toHaveBeenCalledWith("Live");
    await vi.advanceTimersByTimeAsync(1000);
    expect(requests[1]).toContain("after=epoch%3A7");
    await vi.advanceTimersByTimeAsync(2000);
    expect(requests[2]).not.toContain("after=");
    close();
    await vi.advanceTimersByTimeAsync(30000);
    expect(requests).toHaveLength(3);
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
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(`data: ${JSON.stringify(envelope)}\n\n`)),
  );
  const { watchSummary } = await import("./events");
  const received = vi.fn();
  const transport = createTransport("test", () => {});
  const stop = watchSummary(transport, received, () => {});
  try {
    await vi.waitFor(() => expect(received).toHaveBeenCalledWith(event));
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
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(request.url);
      const frame =
        requests.length === 1
          ? { kind: "open", resume_cursor: "epoch:7" }
          : { kind: "invalidation", resume_cursor: "epoch:8", event: notice };
      return new Response(`data: ${JSON.stringify(frame)}\n\n`);
    }),
  );
  const { watchSummary } = await import("./events");
  const transport = createTransport("", vi.fn());
  const receive = vi.fn();
  const close = watchSummary(transport, receive, vi.fn());
  try {
    await vi.advanceTimersByTimeAsync(0);
    close.retry();
    close.retry();
    await vi.advanceTimersByTimeAsync(0);
    expect(requests).toHaveLength(2);
    expect(requests[1]).toContain("after=epoch%3A7");
    expect(receive).toHaveBeenCalledWith(notice);
    close();
    close.retry();
    await vi.advanceTimersByTimeAsync(30000);
    expect(requests).toHaveLength(2);
  } finally {
    close();
    transport.close();
    vi.useRealTimers();
  }
});
