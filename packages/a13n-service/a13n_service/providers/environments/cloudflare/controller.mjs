// Native Container lifecycle and EIP byte forwarding. No Agent command/file implementation.
import { setupApplication } from "./setup.mjs";
export async function digest(value) {
  return [
    ...new Uint8Array(
      await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)),
    ),
  ]
    .map((x) => x.toString(16).padStart(2, "0"))
    .join("");
}
export async function authenticate(request, expected) {
  const value =
    request.headers.get("X-A13N-Bridge") ??
    request.headers.get("Authorization")?.replace(/^Bearer /, "");
  return (
    typeof value === "string" &&
    value.length === 64 &&
    (await digest(value)) === expected
  );
}
function json(value, status = 200) {
  return Response.json(value, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}
const QUIESCE = `const fs=require('fs');const p='/var/lib/a13n-cloudflare/';fs.rmSync(p+'quiesce-failed',{force:true});fs.writeFileSync(p+'quiesce','1',{mode:384});const d=Date.now()+40000;const t=setInterval(()=>{if(fs.existsSync(p+'quiesced')){clearInterval(t);process.exit(0)}if(fs.existsSync(p+'quiesce-failed')||Date.now()>d){clearInterval(t);process.exit(1)}},100);`;
export class Controller {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    this.tail = Promise.resolve();
    this.active = 0;
  }
  async initialize() {
    this.state = await this.ctx.storage.get("state");
    if (this.ctx.container?.running) {
      await this.ctx.container.setInactivityTimeout(600000);
      await this.ctx.storage.setAlarm(Date.now() + 60000);
    }
  }
  exclusive(fn) {
    const result = this.tail.then(fn);
    this.tail = result.catch(() => {});
    return result;
  }
  async save() {
    await this.ctx.storage.put("state", this.state);
  }
  view() {
    const s = this.state;
    let status = s.status;
    if (status === "running" && !this.ctx.container.running) status = "lost";
    return {
      environment_id: s.environment_id,
      fingerprint: s.fingerprint,
      status,
      version: s.version,
      snapshot_id: s.snapshot?.id ?? null,
      snapshot_expires_at: s.snapshotExpires ?? null,
    };
  }
  async fetch(request) {
    const path = new URL(request.url).pathname;
    if (path === "/setup" && request.method === "POST")
      return this.exclusive(() =>
        setupApplication(this.ctx, this.env, request),
      );
    const owner = request.headers.get("X-A13N-Environment");
    const fingerprint = request.headers.get("X-A13N-Fingerprint");
    if (
      this.state &&
      (this.state.environment_id !== owner ||
        this.state.fingerprint !== fingerprint)
    )
      return json({ error: "identity_conflict" }, 409);
    if (path === "/control/inspect" && request.method === "GET")
      return json(
        this.state
          ? this.view()
          : {
              environment_id: owner,
              fingerprint,
              status: "absent",
              version: 0,
            },
      );
    if (path === "/control/credential" && request.method === "GET") {
      if (!this.state || this.view().status !== "running")
        return json({ error: "not_running" }, 409);
      return json({ token: this.state.token });
    }
    if (path.startsWith("/eip/")) return this.proxy(request);
    if (
      request.method !== "POST" ||
      !/^\/control\/(create|start|stop|destroy|keepalive)$/.test(path)
    )
      return new Response(null, { status: 404 });
    if (Number(request.headers.get("content-length") ?? 0) > 32768)
      return new Response(null, { status: 413 });
    const text = await request.text();
    if (text.length > 32768) return new Response(null, { status: 413 });
    let body;
    try {
      body = JSON.parse(text);
    } catch {
      return new Response(null, { status: 400 });
    }
    if (
      !body.operation_id ||
      !/^[A-Za-z0-9_.:-]{1,128}$/.test(body.operation_id)
    )
      return new Response(null, { status: 400 });
    return this.exclusive(async () => {
      try {
        return await this.operation(
          path.split("/").at(-1),
          owner,
          fingerprint,
          body,
        );
      } catch {
        return json({ error: "operation_unconfirmed" }, 503);
      }
    });
  }
  async operation(action, owner, fingerprint, body) {
    if (
      this.state &&
      (this.state.environment_id !== owner ||
        this.state.fingerprint !== fingerprint)
    )
      return json({ error: "identity_conflict" }, 409);
    const previous = await this.ctx.storage.get("op:" + body.operation_id);
    if (previous)
      return previous === action
        ? json(this.view())
        : json({ error: "operation_conflict" }, 409);
    if (!this.state) {
      if (action !== "create") return json({ error: "missing" }, 404);
      if (
        !/^[0-9a-f]{64}$/.test(fingerprint ?? "") ||
        ![
          "lite",
          "standard-1",
          "standard-2",
          "standard-3",
          "standard-4",
        ].includes(body.instance)
      )
        return json({ error: "invalid" }, 400);
      this.state = {
        environment_id: owner,
        fingerprint,
        status: "starting",
        version: 0,
        instance: body.instance,
        token: crypto.randomUUID() + crypto.randomUUID(),
        lease: Date.now() + 300000,
        pending: body.operation_id,
      };
      await this.save();
    }
    if (this.state.status === "absent" && action !== "destroy")
      return json({ error: "deleted" }, 409);
    if (
      this.state.pending &&
      this.state.pending !== body.operation_id &&
      action !== "destroy"
    )
      return json({ error: "pending" }, 409);
    if (body.version !== this.state.version && action !== "keepalive")
      return json({ error: "version_conflict" }, 409);
    if (action === "keepalive") {
      if (this.view().status !== "running")
        return json({ error: "not_running" }, 409);
      const until = Date.parse(body.deadline);
      if (
        !Number.isFinite(until) ||
        until > Date.now() + 3600000 ||
        until < Date.now()
      )
        return json({ error: "deadline" }, 400);
      this.state.lease = Math.max(this.state.lease, until);
      await this.save();
      await this.ctx.container.setInactivityTimeout(600000);
      await this.ctx.storage.setAlarm(Date.now() + 60000);
      return json(this.view());
    }
    this.state.pending = body.operation_id;
    await this.save();
    if (action === "create" || action === "start") {
      if (action === "create" && this.state.status !== "starting")
        return json({ error: "already_created" }, 409);
      if (!this.ctx.container.running) {
        if (action === "start" && !this.state.snapshot)
          return json({ error: "snapshot_missing" }, 409);
        if (
          this.state.snapshotExpires &&
          Date.parse(this.state.snapshotExpires) <= Date.now()
        )
          return json({ error: "snapshot_expired" }, 409);
        const source = this.state.snapshot
          ? { containerSnapshot: this.state.snapshot }
          : { image: "cloudflare/debian-trixie" };
        this.ctx.container.start({
          ...source,
          instance: this.state.instance,
          enableInternet: true,
          entrypoint: [
            "node",
            "--input-type=module",
            "-e",
            this.env.GUEST_SOURCE,
          ],
          env: {
            A13N_GUEST_TOKEN: this.state.token,
            A13N_ENVD_DEVICE_ID: owner,
          },
        });
        await this.ctx.container.setInactivityTimeout(600000);
        if (this.state.snapshot)
          this.state.snapshotExpires = new Date(
            Date.now() + 30 * 86400000,
          ).toISOString();
      }
      // Container running is not Envd readiness; the Python management adapter probes EIP.
      this.state.status = "running";
      this.state.lease = Date.now() + 300000;
      await this.ctx.storage.setAlarm(Date.now() + 60000);
    } else if (action === "stop") {
      await this.stop();
    } else if (action === "destroy") {
      this.state.status = "absent";
      await this.save();
      if (this.ctx.container.running) await this.ctx.container.destroy();
      delete this.state.snapshot;
      delete this.state.token;
      await this.ctx.storage.deleteAlarm();
    }
    this.state.version++;
    delete this.state.pending;
    await this.ctx.storage.transaction(async (tx) => {
      await tx.put("state", this.state);
      await tx.put("op:" + body.operation_id, action);
    });
    return json(this.view());
  }
  async stop() {
    if (this.state.status === "stopped") return;
    if (!this.ctx.container.running) {
      if (this.state.savedForStop) {
        this.state.status = "stopped";
        delete this.state.savedForStop;
        await this.save();
        return;
      }
      throw new Error("lost_without_suspend");
    }
    this.state.status = "stopping";
    await this.save();
    const deadline = Date.now() + 45000;
    while (this.active && Date.now() < deadline)
      await new Promise((r) => setTimeout(r, 25));
    if (this.active) throw new Error("transfers_not_drained");
    if (!this.state.savedForStop) {
      const process = await this.ctx.container.exec(["node", "-e", QUIESCE], {
        stdout: "ignore",
        stderr: "ignore",
      });
      if ((await process.exitCode) !== 0) throw new Error("quiesce_failed");
      const snapshot = await this.ctx.container.snapshotContainer({});
      this.state.snapshot = snapshot;
      this.state.snapshotExpires = new Date(
        Date.now() + 30 * 86400000,
      ).toISOString();
      this.state.savedForStop = true;
      await this.save();
    }
    await this.ctx.container.destroy();
    this.state.status = "stopped";
    delete this.state.savedForStop;
    await this.save();
    await this.ctx.storage.deleteAlarm();
  }
  async alarm() {
    return this.exclusive(async () => {
      if (
        !this.state ||
        this.state.status === "absent" ||
        this.state.status === "stopped"
      )
        return;
      if (!this.ctx.container.running) return;
      await this.ctx.container.setInactivityTimeout(600000);
      // Keep retrying a failed snapshot rather than intentionally losing the disk.
      await this.ctx.storage.setAlarm(Date.now() + 60000);
      if (
        this.state.status === "stopping" ||
        (!this.state.pending && Date.now() > this.state.lease)
      ) {
        try {
          await this.stop();
        } catch {
          /* No unsafe destructive fallback. */
        }
      }
    });
  }
  async proxy(request) {
    if (!this.state || this.view().status !== "running" || this.state.pending)
      return new Response(null, { status: 409 });
    if (request.headers.get("Authorization") !== "Bearer " + this.state.token)
      return new Response(null, { status: 401 });
    const url = new URL(request.url);
    if (url.search || !/^\/eip\/(control|transfer)$/.test(url.pathname))
      return new Response(null, { status: 404 });
    const headers = new Headers(request.headers);
    for (const key of [
      "X-A13N-Bridge",
      "X-A13N-Environment",
      "X-A13N-Fingerprint",
    ])
      headers.delete(key);
    this.active++;
    try {
      const result = await this.ctx.container.getTcpPort(8888).fetch(
        new Request("http://container" + url.pathname, {
          method: request.method,
          headers,
          body: request.body,
          redirect: "manual",
          duplex: "half",
        }),
      );
      if (!result.body) {
        this.active--;
        return result;
      }
      const reader = result.body.getReader();
      let ended = false;
      const finish = () => {
        if (ended) return false;
        ended = true;
        this.active--;
        return true;
      };
      const stream = new ReadableStream({
        async pull(controller) {
          try {
            const { done, value } = await reader.read();
            if (done) {
              if (finish()) controller.close();
            } else controller.enqueue(value);
          } catch (error) {
            if (finish()) controller.error(error);
          }
        },
        async cancel(reason) {
          finish();
          await reader.cancel(reason);
        },
      });
      return new Response(stream, {
        status: result.status,
        headers: result.headers,
      });
    } catch {
      this.active--;
      return new Response(null, { status: 502 });
    }
  }
}
