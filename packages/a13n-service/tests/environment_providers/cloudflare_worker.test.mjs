import test from "node:test";
import assert from "node:assert/strict";
import {
  Controller,
  authenticate,
  digest,
} from "../../a13n_service/providers/environments/cloudflare/controller.mjs";
import { setupApplication } from "../../a13n_service/providers/environments/cloudflare/setup.mjs";

function fixture() {
  const records = new Map();
  const events = [];
  const storage = {
    async get(k) {
      return structuredClone(records.get(k));
    },
    async put(k, v) {
      records.set(k, structuredClone(v));
      events.push("put:" + k);
    },
    async transaction(fn) {
      return fn(this);
    },
    async setAlarm() {},
    async deleteAlarm() {},
  };
  const container = {
    running: false,
    starts: [],
    snapshots: 0,
    destroys: 0,
    async setInactivityTimeout() {},
    start(options) {
      this.running = true;
      this.starts.push(options);
    },
    async exec() {
      events.push("quiesce");
      return { exitCode: Promise.resolve(0) };
    },
    async snapshotContainer() {
      events.push("snapshot");
      this.snapshots++;
      return { id: "snapshot-1" };
    },
    async destroy() {
      assert(
        records.get("state").snapshot ||
          records.get("state").status === "absent",
      );
      events.push("destroy");
      this.destroys++;
      this.running = false;
    },
    getTcpPort() {
      return {
        fetch: async (request) =>
          new Response(request.body, {
            headers: { "Content-Type": "application/octet-stream" },
          }),
      };
    },
  };
  const ctx = { storage, container };
  const controller = new Controller(ctx, {
    GUEST_SOURCE: "guest",
    SCRIPT_NAME: "script",
    ACCOUNT_ID: "account",
  });
  return { controller, ctx, container, records, events };
}
const owner = "env_test";
const fingerprint = "a".repeat(64);
function request(action, op = "op_1", version = 0) {
  return new Request("https://example/control/" + action, {
    method: "POST",
    headers: { "X-A13N-Environment": owner, "X-A13N-Fingerprint": fingerprint },
    body: JSON.stringify({ operation_id: op, version, instance: "standard-1" }),
  });
}
async function create(f) {
  assert.equal((await f.controller.fetch(request("create"))).status, 200);
}

test("create is idempotent; conflicting recipes cannot attach", async () => {
  const f = fixture();
  await create(f);
  await create(f);
  assert.equal(f.container.starts.length, 1);
  const changed = request("start", "op_2", 1);
  changed.headers.set("X-A13N-Fingerprint", "b".repeat(64));
  assert.equal((await f.controller.fetch(changed)).status, 409);
});
test("suspend persists snapshot before destroy and resumes without image or command replay", async () => {
  const f = fixture();
  await create(f);
  assert.equal(
    (await f.controller.fetch(request("stop", "op_stop", 1))).status,
    200,
  );
  assert(f.events.indexOf("snapshot") < f.events.indexOf("destroy"));
  assert.equal(f.records.get("state").status, "stopped");
  assert.equal(
    (await f.controller.fetch(request("start", "op_start", 2))).status,
    200,
  );
  assert.deepEqual(f.container.starts[1].containerSnapshot, {
    id: "snapshot-1",
  });
  assert.equal(f.container.starts[1].image, undefined);
  assert.equal(f.container.starts[1].env.A13N_ENVD_DEVICE_ID, owner);
  assert.equal(f.container.starts[1].envVars, undefined);
});
test("snapshot failure never destroys; retry uses the same operation", async () => {
  const f = fixture();
  await create(f);
  const snapshot = f.container.snapshotContainer;
  f.container.snapshotContainer = async () => {
    throw new Error("unavailable");
  };
  assert.equal(
    (await f.controller.fetch(request("stop", "stop", 1))).status,
    503,
  );
  assert.equal(f.container.destroys, 0);
  f.container.snapshotContainer = snapshot;
  assert.equal(
    (await f.controller.fetch(request("stop", "stop", 1))).status,
    200,
  );
});
test("quiesce failure refuses snapshot and shutdown", async () => {
  const f = fixture();
  await create(f);
  f.container.exec = async () => ({ exitCode: Promise.resolve(1) });
  assert.equal(
    (await f.controller.fetch(request("stop", "stop", 1))).status,
    503,
  );
  assert.equal(f.container.snapshots, 0);
  assert.equal(f.container.destroys, 0);
});
test("destroy response loss reconciles the tombstone and still stops the live instance", async () => {
  const f = fixture();
  await create(f);
  const destroy = f.container.destroy;
  f.container.destroy = async () => {
    throw new Error("lost");
  };
  assert.equal(
    (await f.controller.fetch(request("destroy", "delete", 1))).status,
    503,
  );
  f.container.destroy = destroy;
  assert.equal(
    (await f.controller.fetch(request("destroy", "delete", 1))).status,
    200,
  );
  assert.equal(f.container.running, false);
  assert.equal(
    (await f.controller.fetch(request("start", "stale", 2))).status,
    409,
  );
});
test("restart after snapshot persistence never snapshots twice", async () => {
  const f = fixture();
  await create(f);
  const destroy = f.container.destroy;
  f.container.destroy = async () => {
    throw new Error("lost");
  };
  await f.controller.fetch(request("stop", "stop", 1));
  const restarted = new Controller(f.ctx, { GUEST_SOURCE: "guest" });
  await restarted.initialize();
  f.container.destroy = destroy;
  assert.equal((await restarted.fetch(request("stop", "stop", 1))).status, 200);
  assert.equal(f.container.snapshots, 1);
});
test("unexpected instance loss never creates an empty replacement", async () => {
  const f = fixture();
  await create(f);
  f.container.running = false;
  assert.equal(f.controller.view().status, "lost");
  assert.equal(
    (await f.controller.fetch(request("start", "start", 1))).status,
    409,
  );
  assert.equal(f.container.starts.length, 1);
});
test("expired snapshot refuses restore", async () => {
  const f = fixture();
  await create(f);
  await f.controller.fetch(request("stop", "stop", 1));
  f.controller.state.snapshotExpires = "2000-01-01T00:00:00Z";
  assert.equal(
    (await f.controller.fetch(request("start", "start", 2))).status,
    409,
  );
  assert.equal(f.container.starts.length, 1);
});
test("old lifecycle version cannot stop a newer incarnation", async () => {
  const f = fixture();
  await create(f);
  assert.equal(
    (await f.controller.fetch(request("stop", "late", 0))).status,
    409,
  );
  assert.equal(f.container.destroys, 0);
});
test("EIP file bytes stream unchanged, proxy headers are stripped and no lifecycle is invoked", async () => {
  const f = fixture();
  await create(f);
  let observed;
  f.container.getTcpPort = () => ({
    fetch: async (request) => {
      observed = request;
      return new Response(request.body);
    },
  });
  const req = new Request("https://example/eip/transfer", {
    method: "PUT",
    headers: {
      Authorization: "Bearer " + f.controller.state.token,
      "X-A13N-Bridge": "bridge",
      "X-A13N-Environment": owner,
      "X-A13N-Fingerprint": fingerprint,
      "EIP-Session": "session",
    },
    body: new Uint8Array([0, 255, 1]),
  });
  const response = await f.controller.fetch(req);
  assert.deepEqual(
    new Uint8Array(await response.arrayBuffer()),
    new Uint8Array([0, 255, 1]),
  );
  assert.equal(observed.headers.get("X-A13N-Bridge"), null);
  assert.equal(observed.headers.get("EIP-Session"), "session");
  assert.equal(f.controller.active, 0);
  assert.equal(f.container.starts.length, 1);
});
test("gateway credential does not substitute for guest credential", async () => {
  const f = fixture();
  await create(f);
  const req = new Request("https://example/eip/control", {
    method: "POST",
    headers: { "X-A13N-Environment": owner, "X-A13N-Fingerprint": fingerprint },
    body: "{}",
  });
  assert.equal((await f.controller.fetch(req)).status, 401);
});
test("bridge authentication rejects wrong credentials", async () => {
  const token = "a".repeat(64);
  const hash = await digest(token);
  assert.equal(
    await authenticate(
      new Request("https://example", {
        headers: { Authorization: "Bearer " + token },
      }),
      hash,
    ),
    true,
  );
  assert.equal(
    await authenticate(
      new Request("https://example", {
        headers: { Authorization: "Bearer wrong" },
      }),
      hash,
    ),
    false,
  );
});
test("application submission loss is not replayed", async (t) => {
  const f = fixture();
  let posts = 0;
  t.mock.method(globalThis, "fetch", async (url, options) => {
    if (options.method === "POST") {
      posts++;
      throw new Error("lost response");
    }
    return Response.json({ success: true, result: [] });
  });
  const env = { ACCOUNT_ID: "account", SCRIPT_NAME: "script" };
  const req = () =>
    new Request("https://example/setup", {
      method: "POST",
      headers: { "X-A13N-Cloudflare-Token": "fixture-token" },
    });
  assert.equal((await setupApplication(f.ctx, env, req())).status, 503);
  assert.equal((await setupApplication(f.ctx, env, req())).status, 503);
  assert.equal(posts, 1);
  assert.equal(f.records.get("application").phase, "submitting");
});
test("application ownership conflict does not mutate the other application", async (t) => {
  const f = fixture();
  let posts = 0;
  t.mock.method(globalThis, "fetch", async (url, options) => {
    if (options.method === "POST") posts++;
    return Response.json({
      success: true,
      result: String(url).includes("/namespaces")
        ? [{ id: "owned", script: "script", class: "Environment" }]
        : [
            {
              id: "app",
              name: "script",
              scheduling_policy: "durable_object",
              durable_objects: { namespace_id: "other" },
            },
          ],
    });
  });
  const response = await setupApplication(
    f.ctx,
    { ACCOUNT_ID: "account", SCRIPT_NAME: "script" },
    new Request("https://example/setup", {
      method: "POST",
      headers: { "X-A13N-Cloudflare-Token": "fixture" },
    }),
  );
  assert.equal(response.status, 409);
  assert.equal(posts, 0);
});

test("application reconciliation follows opaque pagination before allocating", async (t) => {
  const f = fixture();
  let pages = 0;
  t.mock.method(globalThis, "fetch", async (input, options) => {
    assert.equal(options.method, "GET");
    const url = new URL(input);
    if (url.pathname.endsWith("/namespaces"))
      return Response.json({
        success: true,
        result: [{ id: "owned", script: "script", class: "Environment" }],
      });
    pages++;
    if (!url.searchParams.has("page_token"))
      return Response.json({
        success: true,
        result: [],
        result_info: { next_page_token: "opaque/a+b" },
      });
    assert.equal(url.searchParams.get("page_token"), "opaque/a+b");
    return Response.json({
      success: true,
      result: [
        {
          id: "app",
          name: "script",
          scheduling_policy: "durable_object",
          durable_objects: { namespace_id: "owned" },
        },
      ],
    });
  });
  const response = await setupApplication(
    f.ctx,
    { ACCOUNT_ID: "account", SCRIPT_NAME: "script" },
    new Request("https://example/setup", {
      method: "POST",
      headers: { "X-A13N-Cloudflare-Token": "fixture" },
    }),
  );
  assert.equal(response.status, 200);
  assert.equal(pages, 2);
  assert.equal(f.records.get("application").id, "app");
});

test("known application rejection can retry after permissions are fixed", async (t) => {
  const f = fixture();
  f.ctx.storage.delete = async (key) => f.records.delete(key);
  let posts = 0;
  t.mock.method(globalThis, "fetch", async (url, options) => {
    if (options.method === "GET")
      return Response.json({ success: true, result: [] });
    posts++;
    return posts === 1
      ? new Response(null, { status: 403 })
      : Response.json({ success: true, result: { id: "app" } });
  });
  const req = () =>
    new Request("https://example/setup", {
      method: "POST",
      headers: { "X-A13N-Cloudflare-Token": "fixture" },
    });
  const env = { ACCOUNT_ID: "account", SCRIPT_NAME: "script" };
  assert.equal((await setupApplication(f.ctx, env, req())).status, 503);
  assert.equal(f.records.has("application"), false);
  assert.equal((await setupApplication(f.ctx, env, req())).status, 202);
  assert.equal(posts, 2);
});

test("alarm retries a failed suspend without losing its operation fence", async () => {
  const f = fixture();
  await create(f);
  const snapshot = f.container.snapshotContainer;
  f.container.snapshotContainer = async () => {
    throw new Error("unavailable");
  };
  assert.equal(
    (await f.controller.fetch(request("stop", "stop", 1))).status,
    503,
  );
  f.container.snapshotContainer = snapshot;
  await f.controller.alarm();
  assert.equal(f.records.get("state").status, "stopped");
  assert.equal(f.records.get("state").pending, "stop");
  assert.equal(
    (await f.controller.fetch(request("stop", "stop", 1))).status,
    200,
  );
  assert.equal(f.container.destroys, 1);
});

test("guest suspend failure preserves PID 1 and permits a later quiesce retry", async () => {
  const { readFile } = await import("node:fs/promises");
  const source = await readFile(
    new URL(
      "../../a13n_service/providers/environments/cloudflare/guest.mjs",
      import.meta.url,
    ),
    "utf8",
  );
  const files = new Map([["/var/lib/a13n-cloudflare/installed", "0.5.1"]]);
  let tick,
    exited = false,
    failSync = true;
  const fs = {
    mkdirSync() {},
    chownSync() {},
    existsSync: (path) => files.has(path),
    writeFileSync: (path, value) => files.set(path, value),
    rmSync: (path) => files.delete(path),
    readdirSync: () => [],
  };
  const child = { on() {}, kill() {}, exitCode: 0, signalCode: null };
  const run = new (Object.getPrototypeOf(async function () {}).constructor)(
    "fs",
    "spawn",
    "execFileSync",
    "createHash",
    "process",
    "setInterval",
    "setTimeout",
    source.replace(/^import .*;\n/gm, ""),
  );
  await run(
    fs,
    () => child,
    (command) => {
      if (command === "sync" && failSync) throw new Error("flush failed");
    },
    null,
    {
      env: { A13N_GUEST_TOKEN: "fixture", A13N_ENVD_DEVICE_ID: "env_test" },
      exit() {
        exited = true;
      },
    },
    (callback) => {
      tick = callback;
    },
    (callback) => callback(),
  );
  files.set("/var/lib/a13n-cloudflare/quiesce", "1");
  await tick();
  assert.equal(exited, false);
  assert.equal(files.has("/var/lib/a13n-cloudflare/quiesced"), false);
  assert.equal(files.has("/var/lib/a13n-cloudflare/quiesce-failed"), true);
  failSync = false;
  files.set("/var/lib/a13n-cloudflare/quiesce", "1");
  await tick();
  assert.equal(exited, false);
  assert.equal(files.has("/var/lib/a13n-cloudflare/quiesced"), true);
});

test("canceling an in-flight transfer releases admission exactly once", async () => {
  const f = fixture();
  await create(f);
  f.container.getTcpPort = () => ({
    fetch: async () => new Response(new ReadableStream({ pull() {} })),
  });
  const response = await f.controller.fetch(
    new Request("https://example/eip/transfer", {
      method: "POST",
      headers: {
        "X-A13N-Environment": owner,
        "X-A13N-Fingerprint": fingerprint,
        Authorization: "Bearer " + f.records.get("state").token,
      },
      body: "bytes",
    }),
  );
  const reader = response.body.getReader();
  const pending = reader.read();
  await reader.cancel();
  await pending;
  assert.equal(f.controller.active, 0);
});
