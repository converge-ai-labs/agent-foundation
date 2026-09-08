import assert from "node:assert/strict";
import test from "node:test";
import {
  ApiError,
  createClient,
  ProtocolError,
  ReplayGapError,
} from "../dist/index.js";
import { decodeSse } from "../dist/streams/sse.js";

const baseUrl = "https://service.example";
const json = (value) =>
  new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  });

test("session mutations require CSRF and preserve null, omission and concurrency headers", async () => {
  const requests = [];
  const client = createClient({
    baseUrl,
    auth: { type: "session" },
    fetch: async (request) => {
      requests.push(request);
      return json({});
    },
  });
  await assert.rejects(
    client.http.PATCH("/api/v1/users/me", { body: { name: "A" } }),
    /CSRF/,
  );
  client.setCsrfToken("csrf-proof");
  await client.http.PATCH("/api/v1/users/me", {
    headers: { "If-Match": '"version"' },
    body: { name: "A" },
  });
  assert.equal(requests[0].headers.get("X-A13N-CSRF-Token"), "csrf-proof");
  assert.equal(requests[0].headers.get("If-Match"), '"version"');
  assert.equal(requests[0].credentials, "same-origin");
  assert.deepEqual(await requests[0].json(), { name: "A" });
  client.close();
  await assert.rejects(client.http.GET("/api/v1/users/me"), {
    name: "AbortError",
  });
});

test("credentials cannot escape through a per-call base URL", async () => {
  let calls = 0;
  const client = createClient({
    baseUrl,
    auth: { type: "bearer", token: "private" },
    fetch: async () => {
      calls++;
      return json({});
    },
  });
  await assert.rejects(
    client.http.GET("/api/v1/users/me", {
      baseUrl: "https://elsewhere.example",
    }),
    /configured Service/,
  );
  assert.equal(calls, 0);
});

test("safe reads retry but unknown mutation outcomes are never replayed", async () => {
  let calls = 0;
  const client = createClient({
    baseUrl,
    auth: { type: "bearer", token: "private" },
    fetch: async () =>
      ++calls === 1
        ? new Response(null, { status: 503, headers: { "Retry-After": "0" } })
        : json({}),
  });
  await client.http.GET("/api/v1/users/me");
  assert.equal(calls, 2);
  calls = 0;
  await assert.rejects(
    client.http.POST("/api/v1/auth/login", {
      body: { email: "a@example.com", password: "password" },
    }),
    ApiError,
  );
  assert.equal(calls, 1);
});

test("binary uploads are not serialized or buffered by the SDK", async () => {
  const binary = new Blob([new Uint8Array([0, 255, 17, 3])]);
  const client = createClient({
    baseUrl,
    auth: { type: "bearer", token: "key" },
    fetch: async (request) => {
      assert.deepEqual(
        new Uint8Array(await request.arrayBuffer()),
        new Uint8Array([0, 255, 17, 3]),
      );
      assert.equal(request.headers.get("Content-Type"), "image/png");
      return json({});
    },
  });
  await client.http.PUT("/api/v1/users/me/avatar", {
    body: binary,
    headers: { "Content-Type": "image/png", "If-Match": '"a"' },
  });
});

function chunks(text) {
  const bytes = new TextEncoder().encode(text);
  return new ReadableStream({
    start(controller) {
      for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
      controller.close();
    },
  });
}

test("SSE handles split UTF-8, CRLF, multiline payloads and cancellation", async () => {
  const frames = [];
  for await (const frame of decodeSse(
    chunks(
      ": heartbeat\r\nid: 2-0\r\nevent: message\r\ndata: 你好\r\ndata: world\r\n\r\n",
    ),
  ))
    frames.push(frame);
  assert.deepEqual(frames, [
    { id: "2-0", event: "message", data: "你好\nworld" },
  ]);
  await assert.rejects(async () => {
    for await (const _ of decodeSse(chunks("data: partial"))) {
    }
  }, ProtocolError);
  let canceled = false;
  const body = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode("data: one\n\n"));
    },
    cancel() {
      canceled = true;
    },
  });
  for await (const _ of decodeSse(body)) break;
  assert.equal(canceled, true);
});

test("Run stream preserves cursor and rejects replay gaps without canceling execution", async () => {
  const requests = [];
  const envelope = {
    schema_version: "1",
    event_id: "evt_one",
    run_id: "run_one",
    thread_id: "th_one",
    event_type: "agui.text_message_content",
    occurred_at: "2026-09-08T00:00:00Z",
    payload: { delta: "hello" },
  };
  const client = createClient({
    baseUrl,
    auth: { type: "session" },
    fetch: async (request) => {
      requests.push(request);
      return new Response(
        chunks(
          `id: 2-0\nevent: ${envelope.event_type}\ndata: ${JSON.stringify(envelope)}\n\nid: 3-0\nevent: run_stream.replay_gap\ndata: {}\n\n`,
        ),
        { headers: { "Content-Type": "text/event-stream" } },
      );
    },
  });
  const stream = client.streamRun("run_one", {
    after: "1-0",
    workspaceId: "ws_one",
  });
  assert.equal((await stream.next()).value.cursor, "2-0");
  await assert.rejects(stream.next(), ReplayGapError);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].headers.get("Last-Event-ID"), "1-0");
  assert.equal(requests[0].method, "GET");
});
