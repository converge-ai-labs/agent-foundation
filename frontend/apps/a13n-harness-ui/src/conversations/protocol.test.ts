import { startApp } from "../../tests/app-fixture";
import { afterAll, beforeAll, expect, it, vi } from "vitest";
import * as Y from "yjs";
import { createTransport, result, type Transport } from "../transport/client";
import { ThreadDraft, values } from "./draft";
import { submitDraft } from "./composer";
import { FocusDisplay, watchThread } from "./stream";

let app: Awaited<ReturnType<typeof startApp>>;
let transport: Transport;
beforeAll(async () => {
  app = await startApp();
  vi.stubGlobal("window", { location: { origin: app.origin } });
  transport = createTransport("test-only-key", () => {
    throw new Error("Unexpected authentication failure");
  });
  const status = await result(transport.client.GET("/api/status"));
  if (status.app?.candidate_error_message)
    throw new Error(status.app.candidate_error_message);
}, 40000);
afterAll(async () => {
  transport?.close();
  await app?.close();
  vi.unstubAllGlobals();
}, 20000);
async function until(predicate: () => boolean) {
  await vi.waitFor(() => expect(predicate(), predicate.toString()).toBe(true), {
    timeout: 10000,
    interval: 20,
  });
}

it("real JS Yjs replicas interoperate with App admission, HTTP metadata and focused SSE", async () => {
  const created = await result(
    transport.client.POST("/api/threads", {
      body: { title: "Protocol conversation" },
    }),
  );
  const thread = created.thread_id;
  const a = new ThreadDraft(),
    b = new ThreadDraft();
  const first = a.connect(transport, thread, () => {}),
    second = b.connect(transport, thread, () => {});
  const display = new FocusDisplay();
  let savedText = "";
  const refreshHistory = async () => {
    const history = await result(
      transport.client.GET("/api/threads/{thread_id}/transcript", {
        params: { path: { thread_id: thread } },
      }),
    );
    savedText = history.entries
      .flatMap((entry) => entry.parts.map((part) => part.text ?? ""))
      .join("\n");
  };
  const watch = watchThread(
    transport,
    thread,
    display,
    () => {},
    () => {},
    () => {
      void refreshHistory();
    },
  );
  try {
    await until(() => a.synchronized && b.synchronized && display.ready);
    a.doc.getText("text").insert(0, "Hello ");
    b.doc.getText("text").insert(0, "world");
    await until(
      () =>
        a.synchronized &&
        b.synchronized &&
        values(a.doc).prompt === values(b.doc).prompt,
    );
    expect(values(a.doc).prompt).toContain("Hello ");
    expect(values(a.doc).prompt).toContain("world");
    const response = await transport.fetch(
      `/api/threads/${thread}/attachments?name=example.txt`,
      { method: "POST", body: "retained attachment" },
    );
    const attachment = await response.json();
    const selected = a.addAttachment("pending", 3);
    await until(() => a.synchronized && b.synchronized);
    expect(() => a.capture()).toThrow("incomplete attachments");
    a.doc.getMap("attachments").set(selected, attachment.attachment_id);
    await until(
      () => a.synchronized && values(b.doc).attachment_ids.length === 1,
    );
    const metadata = await result(
      transport.client.GET(
        "/api/threads/{thread_id}/attachments/{attachment_id}/metadata",
        {
          params: {
            path: {
              thread_id: thread,
              attachment_id: attachment.attachment_id,
            },
          },
        },
      ),
    );
    expect(metadata.name).toBe("example.txt");
    expect(metadata.source).toBeNull();
    a.undo.undo();
    await until(
      () => a.synchronized && values(b.doc).attachment_ids.length === 0,
    );
    a.undo.redo();
    await until(
      () => a.synchronized && values(b.doc).attachment_ids.length === 1,
    );
    const captured = a.capture();
    expect(captured.parts[1]).toEqual({
      attachment_id: attachment.attachment_id,
    });
    const sourceId = "input_0123456789abcdef0123456789abcdef";
    const receipt = await result(
      transport.client.POST("/api/threads/{thread_id}/submit", {
        params: { path: { thread_id: thread } },
        body: { parts: captured.parts, source_id: sourceId },
      }),
    );
    b.doc.getText("text").insert(0, "NEXT");
    a.clear(captured);
    await until(
      () =>
        a.synchronized &&
        b.synchronized &&
        values(a.doc).prompt === "NEXT" &&
        values(b.doc).prompt === "NEXT",
    );
    expect(values(a.doc).attachment_ids).toEqual([]);
    // A fast Run may select final history before the changed-Run bootstrap
    // reaches its reader. Production clients reconcile saved output on hints;
    // live replay is not a durable delivery guarantee.
    await until(
      () =>
        savedText.includes("Protocol response") ||
        [...display.blocks.values()].some((block) =>
          block.text.includes("Protocol response"),
        ),
    );
    expect(display.snapshot?.thread.thread.thread_id).toBe(thread);
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
    const history = await result(
      transport.client.GET("/api/threads/{thread_id}/transcript", {
        params: { path: { thread_id: thread } },
      }),
    );
    const authored = history.entries
      .flatMap((entry) => entry.parts)
      .filter((part) => part.metadata?.source_id === sourceId);
    expect(authored.length).toBeGreaterThanOrEqual(captured.parts.length);
    expect(authored.some((part) => part.text?.includes("example.txt"))).toBe(
      true,
    );
    const retained = await transport.fetch(
      `/api/threads/${thread}/attachments/${attachment.attachment_id}`,
    );
    expect(await retained.text()).toBe("retained attachment");
    expect(
      history.entries.some((entry) =>
        entry.parts.some(
          (part) => part.kind === "user" && part.text?.includes("example.txt"),
        ),
      ),
    ).toBe(true);
    expect(
      history.entries.some((entry) =>
        entry.parts.some(
          (part) =>
            part.text?.includes("Protocol response") && part.comment_target,
        ),
      ),
    ).toBe(true);
    first.presence({ name: "Alice", color: "#112233" });
    await until(() =>
      Object.values(b.participants).some((person) => person.name === "Alice"),
    );
    // A deletion-only edit must round-trip through the server before Send is enabled.
    a.doc.getText("text").delete(0, 1);
    expect(a.synchronized).toBe(false);
    await until(() => a.synchronized && values(b.doc).prompt === "EXT");
  } finally {
    first.close();
    second.close();
    watch();
  }
}, 20000);

it("an uncertain admission retains input and cannot silently retry", async () => {
  const created = await result(
    transport.client.POST("/api/threads", { body: {} }),
  );
  const draft = new ThreadDraft();
  const joined = draft.connect(transport, created.thread_id, () => {});
  try {
    await until(() => draft.synchronized);
    draft.doc.getText("text").insert(0, "do not retry");
    await until(() => draft.synchronized);
    const original = globalThis.fetch;
    const send = vi
      .spyOn(globalThis, "fetch")
      .mockImplementationOnce(async (...args) => {
        await original(...args);
        throw new TypeError("Lost response after server acceptance");
      });
    try {
      await submitDraft(draft, transport, created.thread_id, "send");
      expect(draft.submission.kind).toBe("unknown");
      expect(values(draft.doc).prompt).toBe("do not retry");
      await submitDraft(draft, transport, created.thread_id, "send");
      expect(send).toHaveBeenCalledTimes(1);
    } finally {
      send.mockRestore();
    }
  } finally {
    joined.close();
  }
}, 15000);

it("delivers actual final output over the global SSE after the Host settles the exact root receipt", async () => {
  const { watchSummary } = await import("../transport/events");
  const events: import("../transport/client").Schema<"SummaryInvalidation">[] =
    [];
  let connected = false;
  const close = watchSummary(
    transport,
    (event) => {
      if (event) events.push(event);
    },
    (state) => {
      connected = state === "Live";
    },
  );
  try {
    await until(() => connected);
    const thread = await result(
      transport.client.POST("/api/threads", {
        body: { title: "Notification protocol" },
      }),
    );
    const receipt = await result(
      transport.client.POST("/api/threads/{thread_id}/submit", {
        params: { path: { thread_id: thread.thread_id } },
        body: { parts: ["Complete this turn."] },
      }),
    );
    await until(() =>
      events.some((event) => event.notice?.receipt_id === receipt.receipt_id),
    );
    const notices = events.filter(
      (event) => event.notice?.receipt_id === receipt.receipt_id,
    );
    expect(notices).toHaveLength(1);
    expect(notices[0]).toMatchObject({
      kind: "root_operation",
      root_thread_id: thread.thread_id,
      notice: { status: "completed", brief: "Protocol response" },
    });
    const operation = await result(
      transport.client.GET("/api/operations/{receipt_id}", {
        params: { path: { receipt_id: receipt.receipt_id } },
      }),
    );
    expect(operation.status).toBe("completed");
    expect(operation.outcome?.continuation.status).toBe("selected");
  } finally {
    close();
  }
});

it("selects a model for one HTTP admission without changing sticky configuration and rejects overrides on steering", async () => {
  const catalog = await result(transport.client.GET("/api/selectors"));
  expect(catalog.models?.map((item) => item.model_id)).toContain(
    "model-alternate",
  );
  const created = await result(
    transport.client.POST("/api/threads", { body: {} }),
  );
  const thread = created.thread_id;
  const receipt = await result(
    transport.client.POST("/api/threads/{thread_id}/submit", {
      params: { path: { thread_id: thread } },
      body: { prompt: "Use the alternate model", model_id: "model-alternate" },
    }),
  );
  await expect(
    transport.fetch(`/api/operations/${receipt.receipt_id}/steer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: "Continue", model_id: "model-fixture" }),
    }),
  ).rejects.toMatchObject({ status: 400 });
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
  const inspection = await result(
    transport.client.GET("/api/threads/{thread_id}/configuration", {
      params: { path: { thread_id: thread } },
    }),
  );
  expect(inspection.captured?.agent.model_id).toBe("model-alternate");
  expect(inspection.next_model_id).toBe("model-fixture");
  const rejected = await transport.client.POST(
    "/api/threads/{thread_id}/submit",
    {
      params: { path: { thread_id: thread } },
      body: { prompt: "Do not silently fall back", model_id: "missing-model" },
    },
  );
  expect(rejected.data?.receipt_id).toBeTruthy();
  await vi.waitFor(
    async () => {
      const operation = await result(
        transport.client.GET("/api/operations/{receipt_id}", {
          params: { path: { receipt_id: rejected.data!.receipt_id } },
        }),
      );
      expect(operation.status).toBe("failed");
    },
    { timeout: 10000 },
  );
});

it("projects skills before creation and validates references on submit and active steering", async () => {
  const preview = await result(
    transport.client.POST("/api/threads/skills-preview", { body: {} }),
  );
  expect(preview.context_kind).toBe("draft");
  const skill = preview.items.find(
    (item) => item.name === "harness-ui-configuration",
  )!;
  expect(skill).toBeTruthy();
  const created = await result(
    transport.client.POST("/api/threads", { body: {} }),
  );
  const path = { thread_id: created.thread_id };
  const idle = await result(
    transport.client.GET("/api/threads/{thread_id}/skills", {
      params: { path },
    }),
  );
  expect(idle.context_kind).toBe("idle");
  const ref = {
    catalog_id: preview.catalog_id,
    item_id: skill.item_id,
    name: skill.name,
  };
  await expect(
    transport.client.POST("/api/threads/{thread_id}/submit", {
      params: { path },
      body: {
        prompt: "$missing",
        skill_references: [{ ...ref, name: "missing" }],
      },
    }),
  ).rejects.toThrow("Skill is unavailable");
  await expect(
    transport.client.POST("/api/threads/{thread_id}/submit", {
      params: { path },
      body: {
        prompt: `$${skill.name}`,
        skill_references: [
          { ...ref, catalog_id: idle.catalog_id, item_id: "0".repeat(64) },
        ],
      },
    }),
  ).rejects.toThrow("Skill is unavailable");
  const accepted = await result(
    transport.client.POST("/api/threads/{thread_id}/submit", {
      params: { path },
      body: {
        prompt: `Use $${skill.name} and wait for skill inspection`,
        skill_references: [ref],
      },
    }),
  );
  const active = await result(
    transport.client.GET("/api/threads/{thread_id}/skills", {
      params: { path },
    }),
  );
  expect(active.context_kind).toBe("active");
  expect(active.receipt_id).toBe(accepted.receipt_id);
  await vi.waitFor(
    async () => {
      const operation = await result(
        transport.client.GET("/api/operations/{receipt_id}", {
          params: { path: { receipt_id: accepted.receipt_id } },
        }),
      );
      expect(operation.available_actions).toContain("steer");
    },
    { timeout: 10000 },
  );
  await expect(
    transport.client.POST("/api/operations/{receipt_id}/steer", {
      params: { path: { receipt_id: accepted.receipt_id } },
      body: {
        prompt: "$missing",
        skill_references: [{ ...ref, name: "missing" }],
      },
    }),
  ).rejects.toThrow("Skill is unavailable");
  const steered = await result(
    transport.client.POST("/api/operations/{receipt_id}/steer", {
      params: { path: { receipt_id: accepted.receipt_id } },
      body: { prompt: `Check $${skill.name}`, skill_references: [ref] },
    }),
  );
  expect(steered.accepted).toBe(true);
  await result(
    transport.client.POST("/api/operations/{receipt_id}/cancel", {
      params: { path: { receipt_id: accepted.receipt_id } },
    }),
  );
});

it("the App resumes an unanswered question after all viewers disconnect, with one shared deadline", async () => {
  const created = await result(
    transport.client.POST("/api/threads", {
      body: { title: "Timed clarification" },
    }),
  );
  const thread = created.thread_id;
  const receipt = await result(
    transport.client.POST("/api/threads/{thread_id}/submit", {
      params: { path: { thread_id: thread } },
      body: { parts: ["ask a timed question"] },
    }),
  );
  await vi.waitFor(
    async () => {
      const operation = await result(
        transport.client.GET("/api/operations/{receipt_id}", {
          params: { path: { receipt_id: receipt.receipt_id } },
        }),
      );
      expect(operation.status).toBe("suspended");
    },
    { timeout: 10000 },
  );
  const viewer = createTransport("test-only-key", () => {});
  const one = await result(
    viewer.client.GET("/api/threads/{thread_id}/decisions", {
      params: { path: { thread_id: thread } },
    }),
  );
  const two = await result(
    transport.client.GET("/api/threads/{thread_id}/decisions", {
      params: { path: { thread_id: thread } },
    }),
  );
  expect(one?.expires_at).toBeTruthy();
  expect(one?.expires_at).toBe(two?.expires_at);
  expect(one?.requests[0].kind).toBe("question");
  viewer.close();
  // No response POST, focus subscription or open decision form keeps this alive.
  await vi.waitFor(
    async () => {
      const history = await result(
        transport.client.GET("/api/threads/{thread_id}/transcript", {
          params: { path: { thread_id: thread } },
        }),
      );
      const text = history.entries
        .flatMap((entry) => entry.parts.map((part) => part.text ?? ""))
        .join("\n");
      expect(text).toContain("Continued:");
      expect(text).toContain("timed out");
      expect(text).toContain("No answer or approval was provided");
    },
    { timeout: 10000 },
  );
  expect(
    await result(
      transport.client.GET("/api/threads/{thread_id}/decisions", {
        params: { path: { thread_id: thread } },
      }),
    ),
  ).toBeNull();
});
