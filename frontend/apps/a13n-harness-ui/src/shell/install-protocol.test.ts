import { spawn } from "node:child_process";
import { once } from "node:events";
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
import { createInterface } from "node:readline";
import { afterAll, beforeAll, expect, it } from "vitest";

let server: ReturnType<typeof spawn>;
let root: string;
let origin: string;
let stderr = "";
beforeAll(async () => {
  root = await mkdtemp(join(tmpdir(), "a13n-install-assets-"));
  await cp(resolve("public"), root, { recursive: true });
  await copyFile(resolve("index.html"), join(root, "index.html"));
  await mkdir(join(root, "assets"));
  await writeFile(
    join(root, "assets", "fixture-123.js"),
    "// immutable fixture",
  );
  server = spawn(
    "uv",
    [
      "run",
      "--locked",
      "--package",
      "a13n-harness-ui",
      "--no-default-groups",
      "python",
      "tests/protocol_server.py",
      "--static-root",
      root,
    ],
    { stdio: ["pipe", "pipe", "pipe"] },
  );
  server.stderr!.on("data", (chunk) => {
    stderr += chunk;
  });
  const lines = createInterface({ input: server.stdout! });
  origin = await new Promise<string>((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error(`Listener timed out: ${stderr}`)),
      30000,
    );
    server.once("exit", (code) => {
      clearTimeout(timer);
      reject(new Error(`Listener exited (${code}): ${stderr}`));
    });
    lines.on("line", (line) => {
      if (line.startsWith("{")) {
        clearTimeout(timer);
        resolve(JSON.parse(line).origin);
      }
    });
  });
}, 40000);
afterAll(async () => {
  if (server?.exitCode === null) {
    const exited = once(server, "exit");
    server.stdin!.end("stop\n");
    const kill = setTimeout(() => server.kill("SIGKILL"), 15000);
    await exited;
    clearTimeout(kill);
  }
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
  expect((await fetch(`${origin}/api/status`)).status).toBe(401);
});

it("keeps root metadata links valid on deep links and keeps hashed assets immutable", async () => {
  for (const path of [
    "/",
    "/new",
    "/new?project=project-one",
    "/new/thread_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "/settings",
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
