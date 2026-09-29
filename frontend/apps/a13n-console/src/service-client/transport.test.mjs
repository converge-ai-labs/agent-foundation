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

test("Thread stream resumes after its last cursor and reports control frames", async () => {
  const requests = [];
  const delta = {
    run_id: "run_one",
    attempt: 1,
    sequence: 2,
    event: { type: "TEXT_MESSAGE_CONTENT", messageId: "m", delta: "hi" },
    item: { id: "itm_one", kind: "text_message", state: "in_progress" },
  };
  const client = createClient({
    baseUrl,
    auth: { type: "session" },
    fetch: async (request) => {
      requests.push(request);
      return new Response(
        chunks(
          `id: 5-0\nevent: delta\ndata: ${JSON.stringify(delta)}\n\nevent: gap\ndata: {"run_id":"run_one"}\n\nevent: changed\ndata: {"version":4}\n\nevent: delta\ndata: {}\n\n`,
        ),
        { headers: { "Content-Type": "text/event-stream" } },
      );
    },
  });
  const stream = client.streamThread("ws_one", "th_one", { after: "4-0" });
  assert.deepEqual((await stream.next()).value, {
    type: "delta",
    cursor: "5-0",
    delta,
  });
  assert.deepEqual((await stream.next()).value, {
    type: "gap",
    run_id: "run_one",
  });
  assert.deepEqual((await stream.next()).value, {
    type: "changed",
    version: 4,
  });
  await assert.rejects(stream.next(), ProtocolError);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, `${baseUrl}/api/v1/threads/th_one/stream`);
  assert.equal(requests[0].headers.get("X-Workspace-ID"), "ws_one");
  assert.equal(requests[0].headers.get("Last-Event-ID"), "4-0");
});

test("reconnects with the consumer's current complete position and matching hint", async () => {
  const requests = [];
  let resume = { run: "run_one", position: "1-100", after: "4-0" };
  const encoder = new TextEncoder();
  const client = createClient({
    baseUrl,
    auth: { type: "session" },
    fetch: async (request) => {
      requests.push(request);
      if (requests.length === 1) {
        let sent = false;
        return new Response(
          new ReadableStream({
            pull(controller) {
              if (sent) controller.error(new Error("Disconnected"));
              else {
                sent = true;
                controller.enqueue(
                  encoder.encode(
                    'event: gap\ndata: {"run_id":"run_one","position":"1-150"}\n\n',
                  ),
                );
              }
            },
          }),
          { headers: { "Content-Type": "text/event-stream" } },
        );
      }
      return new Response(chunks('event: changed\ndata: {"version":5}\n\n'), {
        headers: { "Content-Type": "text/event-stream" },
      });
    },
  });
  const stream = client.streamThread("ws_one", "th_one", {
    resume: () => resume,
  });
  assert.deepEqual((await stream.next()).value, {
    type: "gap",
    run_id: "run_one",
    position: "1-150",
  });
  // A snapshot read healed the missing range before the transport reconnects.
  resume = { run: "run_one", position: "1-160", after: "8-0" };
  assert.deepEqual((await stream.next()).value, {
    type: "changed",
    version: 5,
  });
  await stream.return();
  assert.equal(requests.length, 2);
  assert.equal(new URL(requests[0].url).searchParams.get("position"), "1-100");
  assert.equal(new URL(requests[1].url).searchParams.get("run"), "run_one");
  assert.equal(new URL(requests[1].url).searchParams.get("position"), "1-160");
  assert.equal(requests[1].headers.get("Last-Event-ID"), "8-0");
});
