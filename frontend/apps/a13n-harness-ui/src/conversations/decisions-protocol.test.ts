import { existsSync } from "node:fs";
import { join } from "node:path";
import { afterAll, beforeAll, expect, it, vi } from "vitest";
import { startApp } from "../../tests/app-fixture";
import {
  createTransport,
  result,
  type Schema,
  type Transport,
} from "../transport/client";

let app: Awaited<ReturnType<typeof startApp>>;
let transport: Transport;
beforeAll(async () => {
  app = await startApp("--hitl");
  vi.stubGlobal("window", { location: { origin: app.origin } });
  transport = createTransport("test-only-key", () => {});
}, 30000);
afterAll(async () => {
  transport?.close();
  await app?.close();
  vi.unstubAllGlobals();
});
async function wait(receipt_id: string, status: string) {
  await vi.waitFor(
    async () => {
      const operation = await result(
        transport.client.GET("/api/operations/{receipt_id}", {
          params: { path: { receipt_id } },
        }),
      );
      expect(operation.status, JSON.stringify(operation)).toBe(status);
    },
    { timeout: 15000 },
  );
}
async function pending(prompt: string) {
  const thread = await result(
    transport.client.POST("/api/threads", { body: { title: prompt } }),
  );
  const path = { thread_id: thread.thread_id };
  const first = await result(
    transport.client.POST("/api/threads/{thread_id}/submit", {
      params: { path },
      body: { parts: [prompt] },
    }),
  );
  await wait(first.receipt_id, "suspended");
  const batch = await result(
    transport.client.GET("/api/threads/{thread_id}/decisions", {
      params: { path },
    }),
  );
  expect(batch).toBeTruthy();
  return { path, batch: batch! };
}

it("denies a reviewed shell without executing, then approves another exact request once", async () => {
  const marker = join(app.fixture_root, "approved-marker.txt");
  for (const approved of [false, true]) {
    const { path, batch } = await pending("shell review");
    expect(existsSync(marker)).toBe(false);
    expect(batch.requests).toHaveLength(1);
    const request = batch.requests[0];
    expect(request.kind).toBe("approval");
    if (request.kind !== "approval") throw new Error("Expected approval");
    expect(request.override_allowed).toBe(false);
    expect(request.metadata?.["a13n.harness.tool-review"]).toEqual({
      risk: "high",
      reason: "Writes a fixture marker",
    });
    const body = {
      expected_continuation_id: batch.continuation_id,
      responses: [
        { kind: "approval" as const, request_id: request.request_id, approved },
      ],
    };
    const receipt = await result(
      transport.client.POST("/api/threads/{thread_id}/decisions", {
        params: { path },
        body,
      }),
    );
    await wait(receipt.receipt_id, "completed");
    expect(existsSync(marker)).toBe(approved);
    await expect(
      transport.client.POST("/api/threads/{thread_id}/decisions", {
        params: { path },
        body,
      }),
    ).rejects.toMatchObject({ status: 409 });
    expect(
      await result(
        transport.client.GET("/api/threads/{thread_id}/decisions", {
          params: { path },
        }),
      ),
    ).toBeNull();
  }
}, 30000);

it("requires the complete mixed batch and resumes a generic edited approval with real external and question responses", async () => {
  const { path, batch } = await pending("mixed decisions");
  expect(batch.requests.map((request) => request.kind).sort()).toEqual([
    "approval",
    "external",
    "question",
  ]);
  const approval = batch.requests.find(
    (request) => request.kind === "approval",
  );
  expect(approval?.override_allowed).toBe(true);
  const responses: Schema<"DecisionResponseBatch">["responses"] =
    batch.requests.map((request) => {
      switch (request.kind) {
        case "approval":
          return {
            kind: "approval",
            request_id: request.request_id,
            approved: true,
            override_arguments: { value: "reviewed" },
          };
        case "question":
          return {
            kind: "question",
            request_id: request.request_id,
            answers: { "Where?": "Left" },
          };
        case "external":
          return {
            kind: "external",
            request_id: request.request_id,
            result: { found: true },
          };
      }
    });
  await expect(
    transport.client.POST("/api/threads/{thread_id}/decisions", {
      params: { path },
      body: {
        expected_continuation_id: batch.continuation_id,
        responses: responses.slice(0, 1),
      },
    }),
  ).rejects.toMatchObject({ status: 400 });
  const current = await result(
    transport.client.GET("/api/threads/{thread_id}/decisions", {
      params: { path },
    }),
  );
  expect(current?.continuation_id).toBe(batch.continuation_id);
  const accepted = await result(
    transport.client.POST("/api/threads/{thread_id}/decisions", {
      params: { path },
      body: { expected_continuation_id: batch.continuation_id, responses },
    }),
  );
  await wait(accepted.receipt_id, "completed");
  const history = await result(
    transport.client.GET("/api/threads/{thread_id}/transcript", {
      params: { path },
    }),
  );
  const text = history.entries
    .flatMap((entry) => entry.parts.map((part) => part.text ?? ""))
    .join("\n");
  expect(text).toContain("Published: reviewed");
  expect(text).toContain("found");
  expect(text).toContain("Left");
}, 30000);
