import assert from "node:assert/strict";
import { test } from "vitest";
import { ApiError, createClient, ProtocolError } from "./index.js";
import { decodeSse } from "./streams/sse.js";

const baseUrl = "https://service.example";
const json = (value) =>
  new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  });

test.each([baseUrl, `${baseUrl}/service`])(
  "administrator bootstrap works without a session at %s while protected mutations still require CSRF",
  async (serviceUrl) => {
    const requests = [];
    const client = createClient({
      baseUrl: serviceUrl,
      auth: { type: "session" },
      fetch: async (request) => {
        requests.push(request);
        return json({ principal_id: "usr_admin", csrf_token: "csrf-proof" });
      },
    });
    const body = { email: "admin@example.com", password: "test-password-1234" };
    await client.http.POST("/api/v1/auth/bootstrap", { body });
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, `${serviceUrl}/api/v1/auth/bootstrap`);
    assert.equal(requests[0].headers.get("X-CSRF-Token"), null);
    assert.equal(requests[0].credentials, "same-origin");
    assert.deepEqual(await requests[0].json(), body);
    await assert.rejects(
      client.http.PATCH("/api/v1/users/me", { body: { name: "Admin" } }),
      /CSRF/,
    );
    assert.equal(requests.length, 1);
    client.close();
  },
);

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
  assert.equal(requests[0].headers.get("X-CSRF-Token"), "csrf-proof");
  assert.equal(requests[0].headers.get("If-Match"), '"version"');
  assert.equal(requests[0].credentials, "same-origin");
  assert.deepEqual(await requests[0].json(), { name: "A" });
  client.close();
  await assert.rejects(client.http.GET("/api/v1/users/me"), {
    name: "AbortError",
  });
});

test("first-administrator setup signs in without a CSRF token", async () => {
  const requests = [];
  const client = createClient({
    baseUrl,
    auth: { type: "session" },
    fetch: async (request) => {
      requests.push(request);
      return json({ principal_id: "usr_admin", csrf_token: "csrf-proof" });
    },
  });
  await client.http.POST("/api/v1/auth/bootstrap", {
    body: { email: "admin@example.com", password: "password" },
  });
  assert.equal(requests.length, 1);
  assert.equal(requests[0].headers.get("X-CSRF-Token"), null);
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

test("multipart uploads keep their form boundary through the transport", async () => {
  const form = new FormData();
  form.append("file", new Blob([new Uint8Array([0, 255, 17, 3])]), "a.bin");
  const client = createClient({
    baseUrl,
    auth: { type: "bearer", token: "key" },
    fetch: async (request) => {
      assert.match(
        request.headers.get("Content-Type"),
        /^multipart\/form-data; boundary=/,
      );
      const file = (await request.formData()).get("file");
      assert.deepEqual(
        new Uint8Array(await file.arrayBuffer()),
        new Uint8Array([0, 255, 17, 3]),
      );
      return json({});
    },
  });
  await client.workspace("ws_one").POST("/api/v1/uploads", {
    params: { header: { "Idempotency-Key": "key" } },
    body: { file: form.get("file") },
    bodySerializer: () => form,
  });
});

test("business requests name their workspace; management requests do not", async () => {
  const requests = [];
  const client = createClient({
    baseUrl,
    auth: { type: "session", csrfToken: "csrf-proof" },
    fetch: async (request) => {
      requests.push(request);
      return request.url.endsWith("/avatar?v=2")
        ? new Response(new Blob(["image"], { type: "image/png" }))
        : json({});
    },
  });
  await client.workspace("ws_one").PATCH("/api/v1/agents/{agent_id}", {
    params: { path: { agent_id: "agt_one" } },
    headers: { "If-Match": '"agt_one:1"' },
    body: { name: "A" },
  });
  const image = await client.workspaceBlob(
    "ws_two",
    "/api/v1/agents/agt_one/avatar?v=2",
  );
  await client.http.GET("/api/v1/workspaces/{workspace_id}", {
    params: { path: { workspace_id: "ws_one" } },
  });
  assert.equal(requests[0].url, `${baseUrl}/api/v1/agents/agt_one`);
  assert.equal(requests[0].headers.get("X-Workspace-ID"), "ws_one");
  assert.equal(requests[0].headers.get("If-Match"), '"agt_one:1"');
  assert.equal(requests[0].headers.get("X-CSRF-Token"), "csrf-proof");
  assert.equal(requests[1].headers.get("X-Workspace-ID"), "ws_two");
  assert.equal(await image.text(), "image");
  assert.equal(requests[2].headers.get("X-Workspace-ID"), null);
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
  }, /ended inside an SSE frame/);
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

const sse = (body) =>
  new Response(chunks(body), {
    headers: { "Content-Type": "text/event-stream" },
  });
const item = {
  id: "item_one",
  ordinal: 1,
  kind: "text_message",
  state: "in_progress",
  first_stream_id: "1-1",
  last_stream_id: "1-1",
  started_at: "2026-10-12T10:00:00Z",
  content: { text: "Hi" },
};
const frames = {
  thread: {
    thread: { id: "th_one" },
    current_run: { id: "run_one" },
    last_run: null,
    entries: [],
  },
  items: {
    thread_id: "th_one",
    run_id: "run_one",
    first: 1,
    items: [item],
    continuation: null,
    complete: false,
  },
  delta: {
    thread_id: "th_one",
    run_id: "run_one",
    attempt: 1,
    sequence: 2,
    event: { type: "TEXT_MESSAGE_CONTENT", messageId: "m", delta: "!" },
    item: null,
  },
  gap: { thread_id: "th_one", run_id: "run_one" },
};
const frame = (event, data = frames[event]) =>
  `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

test("Session stream reports its connection and frames, ignoring unknown events", async () => {
  const requests = [];
  const client = createClient({
    baseUrl,
    auth: { type: "session" },
    fetch: async (request) => {
      requests.push(request);
      return sse(
        frame("thread") +
          frame("items") +
          ": keepalive\n\n" +
          frame("later", {}) +
          frame("delta") +
          frame("gap"),
      );
    },
  });
  const stream = client.streamSession("ws_one", "ses_one");
  const received = [];
  for (let index = 0; index < 6; index++)
    received.push((await stream.next()).value);
  await stream.return();
  assert.deepEqual(received, [
    { type: "connection", state: "connecting" },
    { type: "connection", state: "live" },
    { type: "thread", data: frames.thread },
    { type: "items", data: frames.items },
    { type: "delta", data: frames.delta },
    { type: "gap", data: frames.gap },
  ]);
  assert.equal(requests[0].url, `${baseUrl}/api/v1/sessions/ses_one/stream`);
  assert.equal(requests[0].headers.get("X-Workspace-ID"), "ws_one");
  assert.equal(requests[0].headers.get("Last-Event-ID"), null);
});

test("Session stream reconnects from a fresh snapshot after an end or a busy Service", async (context) => {
  context.onTestFinished(() => (Math.random = random));
  const random = Math.random;
  Math.random = () => 0;
  const responses = [
    () => sse(frame("thread")),
    () => new Response(null, { status: 503, headers: { "Retry-After": "0" } }),
    () => sse(frame("thread")),
  ];
  const client = createClient({
    baseUrl,
    auth: { type: "session" },
    maxReadRetries: 0,
    fetch: async () => responses.shift()(),
  });
  const stream = client.streamSession("ws_one", "ses_one");
  const events = [];
  while (events.filter((event) => event.type === "thread").length < 2)
    events.push((await stream.next()).value);
  await stream.return();
  assert.deepEqual(
    events.map((event) =>
      event.type === "connection" ? event.state : event.type,
    ),
    [
      "connecting",
      "live",
      "thread",
      "reconnecting",
      "reconnecting",
      "live",
      "thread",
    ],
  );
  assert.equal(events[4].error.status, 503);
});

test("Session stream stops at a refusal or an invalid frame", async () => {
  const refused = createClient({
    baseUrl,
    auth: { type: "session" },
    fetch: async () =>
      new Response(
        JSON.stringify({ error: { code: "not_found", message: "No session" } }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      ),
  });
  const stream = refused.streamSession("ws", "ses_missing");
  await stream.next();
  await assert.rejects(stream.next(), ApiError);
  const invalid = createClient({
    baseUrl,
    auth: { type: "session" },
    // A raw event must be valid AG-UI within its envelope.
    fetch: async () =>
      sse(frame("delta", { ...frames.delta, event: { type: "RUN_STARTED" } })),
  });
  const deltas = invalid.streamSession("ws", "ses_one");
  await deltas.next();
  await deltas.next();
  await assert.rejects(deltas.next(), ProtocolError);
});

test("Session streams use the original URL and parse Schedule frames", async () => {
  const schedule = {
    session_id: "ses_one",
    schedule_id: "sch_one",
    configuration: { id: "sch_one", version: 1 },
    progress: {},
  };
  const occurrence = {
    session_id: "ses_one",
    schedule_id: "sch_one",
    occurrence: { id: "occ_one", version: 2, status: "failed" },
  };
  const requests = [];
  const client = createClient({
    baseUrl,
    auth: { type: "session" },
    fetch: async (request) => {
      requests.push(request);
      return sse(frame("schedule", schedule) + frame("occurrence", occurrence));
    },
  });
  const stream = client.streamSession("ws_one", "ses_one");
  await stream.next();
  await stream.next();
  assert.deepEqual((await stream.next()).value, {
    type: "schedule",
    data: schedule,
  });
  assert.deepEqual((await stream.next()).value, {
    type: "occurrence",
    data: occurrence,
  });
  await stream.return();
  assert.equal(requests[0].url, `${baseUrl}/api/v1/sessions/ses_one/stream`);
  client.close();
});
