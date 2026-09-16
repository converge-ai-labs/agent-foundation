import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it, vi } from "vitest";
import { IDBFactory } from "fake-indexeddb";
import { startApp } from "../../tests/app-fixture";
import { createTransport, result } from "../transport/client";
import { ResultTracker } from "./results";

it("recovers the exact unseen saved completion after the real App and browser tracker restart", async () => {
  const root = await mkdtemp(join(tmpdir(), "a13n-results-restart-"));
  let app: Awaited<ReturnType<typeof startApp>> | undefined;
  let transport: ReturnType<typeof createTransport> | undefined;
  vi.stubGlobal("indexedDB", new IDBFactory());
  try {
    app = await startApp("--root", root);
    vi.stubGlobal("window", { location: { origin: app.origin } });
    transport = createTransport("test-only-key", () => {});
    const first = new ResultTracker(transport);
    const thread = await result(
      transport.client.POST("/api/threads", {
        body: { title: "Saved while away" },
      }),
    );
    await first.beforeRun(thread.thread_id);
    await first.refresh();
    await result(
      transport.client.POST("/api/threads/{thread_id}/submit", {
        params: { path: { thread_id: thread.thread_id } },
        body: { prompt: "Complete this task" },
      }),
    );
    await vi.waitFor(
      async () => {
        const detail = await result(
          transport!.client.GET("/api/threads/{thread_id}", {
            params: { path: { thread_id: thread.thread_id } },
          }),
        );
        expect(detail.thread.completion?.version).toBe(1);
      },
      { timeout: 10000 },
    );
    transport.close();
    const port = new URL(app.origin).port;
    await app.close();
    app = await startApp("--root", root, "--port", port);
    transport = createTransport("test-only-key", () => {});
    const reopened = new ResultTracker(transport);
    await reopened.refresh();
    expect(reopened.isUnread(thread.thread_id)).toBe(true);
    const saved = await result(
      transport.client.GET("/api/threads/{thread_id}/transcript", {
        params: { path: { thread_id: thread.thread_id } },
      }),
    );
    expect(saved.completion_version).toBe(1);
    expect(
      saved.entries
        .flatMap((entry) => entry.parts.map((part) => part.text ?? ""))
        .join(" "),
    ).toContain("Protocol response");
    await reopened.acknowledge(thread.thread_id, saved.completion_version!);
    expect(reopened.isUnread(thread.thread_id)).toBe(false);
    // Historical opening in a separate browser begins at the current marker.
    vi.stubGlobal("indexedDB", new IDBFactory());
    const fresh = new ResultTracker(transport);
    await fresh.beforeRun(thread.thread_id);
    await fresh.refresh();
    expect(fresh.isUnread(thread.thread_id)).toBe(false);
  } finally {
    transport?.close();
    await app?.close();
    vi.unstubAllGlobals();
    await rm(root, { recursive: true, force: true });
  }
}, 60000);
