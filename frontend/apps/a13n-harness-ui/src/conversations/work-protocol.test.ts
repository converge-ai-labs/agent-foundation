import { afterAll, beforeAll, expect, it, vi } from "vitest";
import { startApp } from "../../tests/app-fixture";
import {
  createTransport,
  result,
  type Schema,
  type Transport,
} from "../transport/client";
import { watchSummary } from "../transport/events";

let app: Awaited<ReturnType<typeof startApp>>;
let transport: Transport;
beforeAll(async () => {
  app = await startApp("--work");
  vi.stubGlobal("window", { location: { origin: app.origin } });
  transport = createTransport("test-only-key", () => {
    throw new Error("Unexpected authentication failure");
  });
}, 40000);
afterAll(async () => {
  transport?.close();
  await app?.close();
  vi.unstubAllGlobals();
}, 20000);

it("reads complete current work through HTTP after scoped summary hints and restores it on the next Run", async () => {
  const root = await result(
    transport.client.POST("/api/threads", { body: {} }),
  );
  const path = { thread_id: root.thread_id };
  const hints: Schema<"SummaryInvalidation">[] = [];
  let connected = false;
  const close = watchSummary(
    transport,
    (event) => {
      if (event) hints.push(event);
    },
    (state) => {
      connected = state === "Live";
    },
  );
  const read = (include: ("tasks" | "notes")[] = []) =>
    result(
      transport.client.GET("/api/threads/{thread_id}/work", {
        params: { path, query: { include } },
      }),
    );
  const submit = () =>
    result(
      transport.client.POST("/api/threads/{thread_id}/submit", {
        params: { path },
        body: { parts: ["Observe work"] },
      }),
    );
  try {
    await vi.waitFor(() => expect(connected).toBe(true));
    const receipt = await submit();
    await vi.waitFor(
      () =>
        expect(
          hints.some(
            (hint) =>
              hint.kind === "thread_work" &&
              hint.thread_id === root.thread_id &&
              hint.work_sections?.includes("notes") &&
              (hint.work_revision ?? 0) > 1,
          ),
        ).toBe(true),
      { timeout: 10000 },
    );
    const live = await read(["tasks", "notes"]);
    expect(live.source).toBe("live");
    expect(live.tasks.total).toBe(1);
    expect(live.notes.page?.notes?.[0].value).toBe("Owner-published work");
    expect(JSON.stringify(hints)).not.toContain("Owner-published work");
    expect((await read()).notes.page).toBeNull();
    await vi.waitFor(
      async () => {
        const operation = await result(
          transport.client.GET("/api/operations/{receipt_id}", {
            params: { path: { receipt_id: receipt.receipt_id } },
          }),
        );
        expect(operation.status).toBe("completed");
      },
      { timeout: 10000 },
    );
    const saved = await read();
    expect(saved.source).toBe("saved");
    expect(saved.tasks.total).toBe(1);
    await submit();
    await vi.waitFor(
      async () => {
        const restored = await read(["tasks"]);
        expect(restored.source).toBe("live");
        expect(restored.run_id).not.toBe(live.run_id);
        expect(restored.base_continuation_id).toBe(saved.continuation_id);
        expect(restored.revision).toBe(1);
        expect(restored.tasks.page?.tasks?.[0].subject).toBe(
          "Review observations",
        );
      },
      { timeout: 10000 },
    );
  } finally {
    close();
  }
}, 20000);
