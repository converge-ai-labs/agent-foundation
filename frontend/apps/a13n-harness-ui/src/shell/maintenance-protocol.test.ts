import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it, vi } from "vitest";
import { startApp } from "../../tests/app-fixture";
import { createTransport, result, type Transport } from "../transport/client";

it("a real server process saves and consumes one planned-update handoff without browser replay", async () => {
  const root = await mkdtemp(join(tmpdir(), "a13n-update-"));
  let app: Awaited<ReturnType<typeof startApp>> | undefined;
  let transport: Transport | undefined;
  async function start() {
    app = await startApp("--root", root, "--update");
    vi.stubGlobal("window", { location: { origin: app.origin } });
    transport = createTransport("test-only-key", () => {
      throw new Error("Unexpected authentication failure");
    });
    return transport;
  }
  try {
    let api = await start();
    const thread = await result(
      api.client.POST("/api/threads", { body: { title: "Planned update" } }),
    );
    const params = { path: { thread_id: thread.thread_id } };
    await result(
      api.client.POST("/api/threads/{thread_id}/submit", {
        params,
        body: { prompt: "Run once" },
      }),
    );
    await vi.waitFor(
      async () =>
        expect(
          await readFile(join(root, "update-requests.jsonl"), "utf8"),
        ).toContain('"returns": 0'),
      { timeout: 10000 },
    );
    expect(
      (await result(api.client.POST("/api/maintenance/prepare"))).phase,
    ).toBe("draining");
    await expect(
      result(
        api.client.POST("/api/threads/{thread_id}/submit", {
          params,
          body: { prompt: "Rejected" },
        }),
      ),
    ).rejects.toThrow();
    await writeFile(join(root, "release-update"), "release");
    await vi.waitFor(
      async () =>
        expect((await result(api.client.GET("/api/maintenance"))).phase).toBe(
          "paused",
        ),
      { timeout: 10000 },
    );
    api.close();
    await app!.close();
    api = await start();
    await vi.waitFor(
      async () =>
        expect((await result(api.client.GET("/api/maintenance"))).phase).toBe(
          "finished",
        ),
      { timeout: 10000 },
    );
    await vi.waitFor(
      async () => {
        const history = await result(
          api.client.GET("/api/threads/{thread_id}/transcript", { params }),
        );
        expect(JSON.stringify(history)).toContain(
          "Continued after planned update.",
        );
      },
      { timeout: 10000 },
    );
    expect(
      (await readFile(join(root, "update-requests.jsonl"), "utf8"))
        .trim()
        .split("\n"),
    ).toEqual(['{"returns": 0}', '{"returns": 1}']);
    api.close();
    await app!.close();
    api = await start();
    expect((await result(api.client.GET("/api/maintenance"))).phase).toBe(
      "finished",
    );
    expect(
      (await readFile(join(root, "update-requests.jsonl"), "utf8"))
        .trim()
        .split("\n"),
    ).toHaveLength(2);
    await expect(
      result(
        api.client.POST("/api/maintenance/dismiss", {
          body: { previous_instance_stopped: false as true },
        }),
      ),
    ).rejects.toThrow();
    expect(
      (
        await result(
          api.client.POST("/api/maintenance/dismiss", {
            body: { previous_instance_stopped: true },
          }),
        )
      ).phase,
    ).toBe("idle");
  } finally {
    transport?.close();
    await app?.close();
    vi.unstubAllGlobals();
    await rm(root, { recursive: true, force: true });
  }
}, 60000);
