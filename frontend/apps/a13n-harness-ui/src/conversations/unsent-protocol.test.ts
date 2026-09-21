import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it, vi } from "vitest";
import { startApp } from "../../tests/app-fixture";
import { createTransport, result } from "../transport/client";
import { watchSummary } from "../transport/events";
import { ThreadDraft } from "./draft";

it("discovers disconnected rooms via authenticated summaries, follows peer clears, and resets on App restart", async () => {
  const root = await mkdtemp(join(tmpdir(), "a13n-unsent-"));
  let app: Awaited<ReturnType<typeof startApp>> | undefined;
  let transport: ReturnType<typeof createTransport> | undefined;
  const close: (() => void)[] = [];
  try {
    app = await startApp("--root", root);
    vi.stubGlobal("window", { location: { origin: app.origin } });
    transport = createTransport("test-only-key", () => {});
    const unauthorized = await fetch(`${app.origin}/api/drafts`);
    expect(unauthorized.status).toBe(401);
    const created = await result(
      transport.client.POST("/api/threads", {
        body: { title: "Old unfinished input" },
      }),
    );
    const before = await result(
      transport.client.GET("/api/threads/{thread_id}", {
        params: { path: { thread_id: created.thread_id } },
      }),
    );
    const hints: string[] = [];
    let live = false;
    close.push(
      watchSummary(
        transport,
        (event) => {
          if (event?.kind === "draft") hints.push(event.thread_id!);
        },
        (state) => {
          live = state === "Live";
        },
      ),
    );
    const draft = new ThreadDraft();
    const editor = draft.connect(transport, created.thread_id, () => {});
    close.push(editor.close);
    await vi.waitFor(() => {
      expect(draft.synchronized).toBe(true);
      expect(live).toBe(true);
    });
    draft.doc.getText("text").insert(0, "Do not forget this");
    await vi.waitFor(() => {
      expect(draft.synchronized).toBe(true);
      expect(hints).toEqual([created.thread_id]);
    });
    const summaries = await result(transport.client.GET("/api/drafts"));
    expect(summaries).toHaveLength(1);
    expect(Object.keys(summaries[0]).sort()).toEqual([
      "draft_id",
      "thread_id",
      "unsent_since",
    ]);
    draft.doc.getText("text").insert(0, "Still editing ");
    await vi.waitFor(() => expect(draft.synchronized).toBe(true));
    expect(await result(transport.client.GET("/api/drafts"))).toEqual(
      summaries,
    );
    expect(hints).toHaveLength(1);
    editor.close();
    for (let index = 0; index < 6; index++)
      await result(
        transport.client.POST("/api/threads", {
          body: { title: `Newer ${index}` },
        }),
      );
    // Discovery is independent of visiting the old Thread and of recent pages.
    const reloaded = createTransport("test-only-key", () => {});
    close.push(() => reloaded.close());
    expect(await result(reloaded.client.GET("/api/drafts"))).toEqual(summaries);
    const rows = await result(
      reloaded.client.POST("/api/threads/activity/lookup", {
        body: { thread_ids: summaries.map((item) => item.thread_id) },
      }),
    );
    expect(rows[0].thread.title).toBe("Old unfinished input");
    const after = await result(
      transport.client.GET("/api/threads/{thread_id}", {
        params: { path: { thread_id: created.thread_id } },
      }),
    );
    expect(after.thread.touched_at).toBe(before.thread.touched_at);
    expect(after.thread.metadata_version).toBe(before.thread.metadata_version);
    const peer = new ThreadDraft();
    const peerEditor = peer.connect(reloaded, created.thread_id, () => {});
    close.push(peerEditor.close);
    await vi.waitFor(() =>
      expect(peer.synchronized && peer.hasUnsentInput).toBe(true),
    );
    peer.doc.getText("text").delete(0, peer.doc.getText("text").length);
    await vi.waitFor(async () => {
      expect(await result(reloaded.client.GET("/api/drafts"))).toEqual([]);
      expect(hints).toHaveLength(2);
    });
    peer.addAttachment("failed");
    await vi.waitFor(() => expect(peer.synchronized).toBe(true));
    expect(await result(reloaded.client.GET("/api/drafts"))).toHaveLength(1);
    for (const stop of close.splice(0)) stop();
    transport.close();
    const port = new URL(app.origin).port;
    await app.close();
    app = await startApp("--root", root, "--port", port);
    transport = createTransport("test-only-key", () => {});
    expect(await result(transport.client.GET("/api/drafts"))).toEqual([]);
  } finally {
    for (const stop of close) stop();
    transport?.close();
    await app?.close();
    vi.unstubAllGlobals();
    await rm(root, { recursive: true, force: true });
  }
}, 60000);
