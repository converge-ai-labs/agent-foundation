import { afterAll, beforeAll, expect, it, vi } from "vitest";
import { startApp } from "../../tests/app-fixture";
import { createTransport, result, type Transport } from "../transport/client";
import { ThreadDraft } from "./draft";
import { submitDraft } from "./composer";

let app: Awaited<ReturnType<typeof startApp>>;
let transport: Transport;
beforeAll(async () => {
  app = await startApp("--goal");
  vi.stubGlobal("window", { location: { origin: app.origin } });
  transport = createTransport("test-only-key", () => {});
}, 40000);
afterAll(async () => {
  transport?.close();
  await app?.close();
  vi.unstubAllGlobals();
}, 20000);

it("submits private Goal intent through the real App and saves one receipt's verified outcome without displaying audit input", async () => {
  const created = await result(
    transport.client.POST("/api/threads", { body: { title: "Goal protocol" } }),
  );
  const thread = created.thread_id;
  const draft = new ThreadDraft();
  const connection = draft.connect(transport, thread, () => {});
  try {
    await vi.waitFor(() => expect(draft.synchronized).toBe(true));
    draft.doc.getText("text").insert(0, "Verify the complete task");
    draft.mode = "goal";
    await vi.waitFor(() => expect(draft.synchronized).toBe(true));
    expect(await submitDraft(draft, transport, thread, "send")).toBe(true);
    expect(draft.mode).toBe("normal");
    if (draft.submission.kind !== "accepted")
      throw new Error("Missing receipt");
    const receipt = draft.submission.receipt;
    const inspect = () =>
      result(
        transport.client.GET("/api/operations/{receipt_id}", {
          params: { path: { receipt_id: receipt } },
        }),
      );
    await vi.waitFor(
      async () => {
        const operation = await inspect();
        expect(operation.goal?.iteration).toBe(1);
        expect(operation.goal?.status).toBe("checking");
      },
      { timeout: 10000, interval: 20 },
    );
    await vi.waitFor(
      async () => {
        const operation = await inspect();
        expect(operation.status).toBe("completed");
        expect(operation.goal?.status).toBe("verified");
        expect(operation.goal?.iteration).toBe(1);
        expect(operation.goal?.objective).toBe("Verify the complete task");
      },
      { timeout: 10000, interval: 20 },
    );
    const saved = await result(
      transport.client.GET("/api/threads/{thread_id}", {
        params: { path: { thread_id: thread } },
      }),
    );
    expect(saved.thread.goal?.status).toBe("verified");
    const history = await result(
      transport.client.GET("/api/threads/{thread_id}/transcript", {
        params: { path: { thread_id: thread } },
      }),
    );
    const visible = history.entries.flatMap((entry) =>
      entry.parts.filter((part) => part.metadata?.display !== false),
    );
    const text = visible.map((part) => part.text ?? "").join("\n");
    expect(text).toContain("[GOAL_COMPLETE]");
    expect(text).not.toContain("<goal-check>");
    expect(visible.filter((part) => part.kind === "user")).toHaveLength(1);
  } finally {
    connection.close();
  }
});

it("rejects an empty Goal objective without admitting work", async () => {
  const created = await result(
    transport.client.POST("/api/threads", { body: {} }),
  );
  await expect(
    transport.client.POST("/api/threads/{thread_id}/submit", {
      params: { path: { thread_id: created.thread_id } },
      body: { prompt: "  ", mode: "goal" },
    }),
  ).rejects.toMatchObject({ status: 400 });
  const saved = await result(
    transport.client.GET("/api/threads/{thread_id}", {
      params: { path: { thread_id: created.thread_id } },
    }),
  );
  expect(saved.thread.goal).toBeNull();
});
