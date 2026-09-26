import { startApp } from "../../tests/app-fixture";
import { createECDH, createHash } from "node:crypto";
import {
  cp,
  copyFile,
  mkdir,
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterAll, beforeAll, expect, it } from "vitest";

let app: Awaited<ReturnType<typeof startApp>>;
let root: string;
let origin: string;
beforeAll(async () => {
  root = await mkdtemp(join(tmpdir(), "a13n-install-assets-"));
  await cp(resolve("public"), root, { recursive: true });
  await copyFile(resolve("index.html"), join(root, "index.html"));
  await mkdir(join(root, "assets"));
  await writeFile(
    join(root, "assets", "fixture-123.js"),
    "// immutable fixture",
  );
  app = await startApp("--static-root", root);
  origin = app.origin;
}, 40000);
afterAll(async () => {
  await app?.close();
  if (root) await rm(root, { recursive: true, force: true });
}, 20000);

it("serves public install metadata and correctly sized PNGs without changing API authentication", async () => {
  const response = await fetch(`${origin}/manifest.webmanifest`);
  expect(response.status).toBe(200);
  expect(response.headers.get("content-type")).toContain(
    "application/manifest+json",
  );
  expect(response.headers.get("cache-control")).toBe("no-cache");
  const manifest = await response.json();
  expect(manifest).toMatchObject({
    id: "/",
    start_url: "/",
    scope: "/",
    display: "standalone",
    name: "Harness UI",
  });
  expect(manifest.icons).toHaveLength(3);
  for (const icon of [
    ...manifest.icons,
    { src: "/icons/apple-touch-icon.png", sizes: "180x180" },
  ]) {
    const image = await fetch(`${origin}${icon.src}`);
    expect(image.status).toBe(200);
    expect(image.headers.get("content-type")).toBe("image/png");
    expect(image.headers.get("cache-control")).toBe("no-cache");
    const bytes = Buffer.from(await image.arrayBuffer());
    expect(bytes.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
    expect(`${bytes.readUInt32BE(16)}x${bytes.readUInt32BE(20)}`).toBe(
      icon.sizes,
    );
    expect(bytes).toEqual(await readFile(join(root, icon.src)));
  }
  const worker = await fetch(`${origin}/sw.js`);
  expect(worker.status).toBe(200);
  expect(worker.headers.get("content-type")).toContain("text/javascript");
  expect(worker.headers.get("cache-control")).toBe("no-cache");
  expect(await worker.text()).toBe(await readFile(join(root, "sw.js"), "utf8"));
  expect((await fetch(`${origin}/api/status`)).status).toBe(401);
  expect((await fetch(`${origin}/api/push/configuration`)).status).toBe(401);
});

it("authenticates and validates push configuration and subscription CRUD without a provider request", async () => {
  const headers = {
    Authorization: "Bearer test-only-key",
    "Content-Type": "application/json",
  };
  const configuration = await fetch(`${origin}/api/push/configuration`, {
    headers,
  });
  expect(configuration.status).toBe(200);
  expect(
    Buffer.from((await configuration.json()).public_key, "base64url"),
  ).toHaveLength(65);
  const receiver = createECDH("prime256v1");
  const endpoint = "https://fcm.googleapis.com/fcm/send/protocol-fixture";
  const body = {
    endpoint,
    origin,
    keys: {
      p256dh: receiver.generateKeys().toString("base64url"),
      auth: Buffer.alloc(16, 1).toString("base64url"),
    },
  };
  expect(
    (
      await fetch(`${origin}/api/push/subscription`, {
        method: "PUT",
        body: JSON.stringify(body),
        headers: { "Content-Type": "application/json" },
      })
    ).status,
  ).toBe(401);
  expect(
    (
      await fetch(`${origin}/api/push/subscription`, {
        method: "PUT",
        body: JSON.stringify(body),
        headers: { ...headers, Origin: "https://other.example" },
      })
    ).status,
  ).toBe(403);
  const invalid = await fetch(`${origin}/api/push/subscription`, {
    method: "PUT",
    headers,
    body: JSON.stringify({ ...body, endpoint: "https://127.0.0.1/private" }),
  });
  expect(invalid.status).toBe(400);
  const saved = await fetch(`${origin}/api/push/subscription`, {
    method: "PUT",
    headers,
    body: JSON.stringify(body),
  });
  expect(saved.status).toBe(200);
  const id = (await saved.json()).subscription_id;
  expect(id).toBe(createHash("sha256").update(endpoint).digest("hex"));
  const activity = `${origin}/api/push/subscriptions/${id}/activity`;
  expect((await fetch(activity, { method: "POST" })).status).toBe(401);
  expect(
    (
      await fetch(activity, {
        method: "POST",
        headers: { ...headers, Origin: "https://other.example" },
      })
    ).status,
  ).toBe(403);
  expect((await fetch(activity, { method: "POST", headers })).status).toBe(204);

  expect(
    (
      await fetch(`${origin}/api/push/subscriptions/${id}`, {
        method: "DELETE",
        headers,
      })
    ).status,
  ).toBe(204);
  expect(
    (
      await fetch(`${origin}/api/push/subscriptions/${id}`, {
        method: "DELETE",
        headers,
      })
    ).status,
  ).toBe(204);
  expect((await fetch(activity, { method: "POST", headers })).status).toBe(404);
});

it("keeps root metadata links valid on deep links and keeps hashed assets immutable", async () => {
  for (const path of [
    "/",
    "/new",
    "/new?project=project-one",
    "/settings",
    "/settings/models",
    "/settings/notifications",
    "/threads/thread-fixture",
  ]) {
    const response = await fetch(`${origin}${path}`);
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-cache");
    expect(response.headers.get("content-security-policy")).toContain(
      "default-src 'self'",
    );
    const html = await response.text();
    expect(html).toContain('rel="manifest" href="/manifest.webmanifest"');
    expect(html).toContain(
      'rel="apple-touch-icon" href="/icons/apple-touch-icon.png"',
    );
  }
  const response = await fetch(`${origin}/assets/fixture-123.js`);
  expect(response.headers.get("cache-control")).toBe(
    "public, max-age=31536000, immutable",
  );
});

it("does not expose arbitrary root files or turn missing installation assets into HTML", async () => {
  await writeFile(join(root, "private.txt"), "not a public asset");
  for (const path of [
    "/new/thread_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "/icons/unknown.png",
    "/private.txt",
    "/api/unknown",
    "/assets/missing.js",
  ]) {
    const response = await fetch(`${origin}${path}`, {
      headers: { Authorization: "Bearer test-only-key" },
    });
    expect(response.status).toBe(404);
    expect(response.headers.get("content-type")).toContain("application/json");
  }
  await rm(join(root, "manifest.webmanifest"));
  await rm(join(root, "icons/icon-192.png"));
  for (const path of ["/manifest.webmanifest", "/icons/icon-192.png"]) {
    const response = await fetch(`${origin}${path}`);
    expect(response.status).toBe(404);
    expect(response.headers.get("content-type")).toContain("application/json");
  }
});
