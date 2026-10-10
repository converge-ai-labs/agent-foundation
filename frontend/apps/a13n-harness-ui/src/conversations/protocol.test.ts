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

it("real JS Yjs replicas interoperate with App admission, HTTP metadata and focused realtime", async () => {
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
      // A hint can arrive after the selected history moved on; that refresh is
      // discarded, exactly as a production client discards a stale projection.
      void refreshHistory().catch(() => {});
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
        entry.parts.some((part) => part.text?.includes("Protocol response")),
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

it("delivers actual final output over the summary channel after the Host settles the exact root receipt", async () => {
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
      body: {
        parts: ["Use the alternate model"],
        model_id: "model-alternate",
        thinking: "low",
        fast: true,
      },
    }),
  );
  await expect(
    transport.fetch(`/api/operations/${receipt.receipt_id}/steer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        parts: ["Continue"],
        model_id: "model-fixture",
        thinking: "high",
        fast: false,
      }),
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
  expect(inspection.captured?.agent.thinking_summary).toBe("Low");
  expect(inspection.captured?.agent.fast).toBe("on");
  expect(
    catalog.models?.find((item) => item.model_id === "model-alternate")?.fast,
  ).toMatchObject({ supported: true, state: "default" });
  expect(
    catalog.models
      ?.find((item) => item.model_id === "model-alternate")
      ?.thinking?.options.map((option) => option.value),
  ).toEqual([null, "minimal", "low", "medium", "high"]);
  expect(inspection.next_model_id).toBe("model-fixture");
  await expect(
    transport.client.POST("/api/threads/{thread_id}/submit", {
      params: { path: { thread_id: thread } },
      body: { parts: ["Do not silently fall back"], model_id: "missing-model" },
    }),
  ).rejects.toMatchObject({ status: 400, code: "model_missing" });
  const retained = await result(
    transport.client.GET("/api/threads/{thread_id}/configuration", {
      params: { path: { thread_id: thread } },
    }),
  );
  expect(retained.captured).toEqual(inspection.captured);
  expect(retained.next_model_id).toBe("model-fixture");
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
        parts: ["$missing"],
        skill_references: [{ ...ref, name: "missing" }],
      },
    }),
  ).rejects.toThrow("Skill is unavailable");
  await expect(
    transport.client.POST("/api/threads/{thread_id}/submit", {
      params: { path },
      body: {
        parts: [`$${skill.name}`],
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
        parts: [`Use $${skill.name} and wait for skill inspection`],
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
        parts: ["$missing"],
        skill_references: [{ ...ref, name: "missing" }],
      },
    }),
  ).rejects.toThrow("Skill is unavailable");
  const steered = await result(
    transport.client.POST("/api/operations/{receipt_id}/steer", {
      params: { path: { receipt_id: accepted.receipt_id } },
      body: { parts: [`Check $${skill.name}`], skill_references: [ref] },
    }),
  );
  expect(steered.accepted).toBe(true);
  const cancellation = await result(
    transport.client.POST("/api/operations/{receipt_id}/cancel", {
      params: { path: { receipt_id: accepted.receipt_id } },
    }),
  );
  expect(cancellation).toMatchObject({
    receipt_id: accepted.receipt_id,
    accepted: true,
  });
  await vi.waitFor(
    async () => {
      const operation = await result(
        transport.client.GET("/api/operations/{receipt_id}", {
          params: { path: { receipt_id: accepted.receipt_id } },
        }),
      );
      expect(operation.status).toBe("cancelled");
      const detail = await result(
        transport.client.GET("/api/threads/{thread_id}", { params: { path } }),
      );
      expect(detail.thread.root_activity.state).toBe("inactive");
    },
    { timeout: 10000 },
  );
  const replacement = await result(
    transport.client.POST("/api/threads/{thread_id}/submit", {
      params: { path },
      body: { parts: ["wait for skill inspection again"] },
    }),
  );
  const oldStop = await result(
    transport.client.POST("/api/operations/{receipt_id}/cancel", {
      params: { path: { receipt_id: accepted.receipt_id } },
    }),
  );
  expect(oldStop.accepted).toBe(false);
  await vi.waitFor(
    async () => {
      const next = await result(
        transport.client.GET("/api/operations/{receipt_id}", {
          params: { path: { receipt_id: replacement.receipt_id } },
        }),
      );
      expect(next.status).toBe("running");
    },
    { timeout: 10000 },
  );
  await result(
    transport.client.POST("/api/operations/{receipt_id}/cancel", {
      params: { path: { receipt_id: replacement.receipt_id } },
    }),
  );
  await vi.waitFor(
    async () => {
      const next = await result(
        transport.client.GET("/api/operations/{receipt_id}", {
          params: { path: { receipt_id: replacement.receipt_id } },
        }),
      );
      expect(next.status).toBe("cancelled");
    },
    { timeout: 10000 },
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

it("multiplexes real summary and focus replay with independent reset and authenticated resume", async () => {
  const created = await result(
    transport.client.POST("/api/threads", {
      body: { title: "Multiplexed root" },
    }),
  );
  const frames: import("../transport/client").Schema<"RealtimeFrame">[] = [];
  const socket = new WebSocket(
    app.origin.replace("http:", "ws:") + "/api/realtime/connect",
  );
  const send = (value: unknown) => socket.send(JSON.stringify(value));
  socket.onmessage = ({ data }) => {
    const frame = JSON.parse(String(data));
    if (frame.kind === "ping") send({ version: 1, kind: "pong" });
    else frames.push(frame);
  };
  await new Promise<void>((resolve) => {
    socket.onopen = () => resolve();
  });
  try {
    send({ api_key: "test-only-key" });
    send({
      version: 1,
      kind: "subscribe",
      channel: "summary",
      stream: "summary",
    });
    send({
      version: 1,
      kind: "subscribe",
      channel: "root",
      stream: "focus",
      root_thread_id: created.thread_id,
    });
    await until(
      () =>
        frames.some(
          (item) => item.channel === "summary" && item.frame.kind === "open",
        ) &&
        frames.some(
          (item) => item.channel === "root" && item.frame.kind === "snapshot",
        ),
    );
    const initial = frames.find(
      (item) => item.channel === "root" && item.frame.kind === "snapshot",
    )!.frame;
    if (initial.kind !== "snapshot") throw new Error("Missing snapshot");
    send({ version: 1, kind: "unsubscribe", channel: "root" });
    send({
      version: 1,
      kind: "subscribe",
      channel: "resumed",
      stream: "focus",
      root_thread_id: created.thread_id,
      after: initial.resume_cursor,
    });
    const opened = frames.find(
      (item) => item.channel === "summary" && item.frame.kind === "open",
    )!.frame;
    if (opened.kind !== "open") throw new Error("Missing summary open");
    send({ version: 1, kind: "unsubscribe", channel: "summary" });
    send({
      version: 1,
      kind: "subscribe",
      channel: "summary-resumed",
      stream: "summary",
      after: opened.resume_cursor,
    });
    send({
      version: 1,
      kind: "subscribe",
      channel: "invalid",
      stream: "summary",
      after: "invalid",
    });
    await until(() =>
      frames.some(
        (item) => item.channel === "invalid" && item.frame.kind === "reset",
      ),
    );
    expect(
      frames.find((item) => item.channel === "summary-resumed")?.frame,
    ).toMatchObject({ kind: "open", resumed: true });
    const receipt = await result(
      transport.client.POST("/api/threads/{thread_id}/submit", {
        params: { path: { thread_id: created.thread_id } },
        body: { parts: ["Generate a response"] },
      }),
    );
    await until(
      () =>
        frames.some(
          (item) => item.channel === "resumed" && item.frame.kind === "event",
        ) &&
        frames.some(
          (item) =>
            item.channel === "summary-resumed" &&
            item.frame.kind === "invalidation" &&
            item.frame.event.notice?.receipt_id === receipt.receipt_id,
        ),
    );
    expect(
      frames
        .filter((item) => item.channel === "resumed")
        .every((item) => item.frame.kind === "event"),
    ).toBe(true);
    const rows = await result(
      transport.client.POST("/api/threads/activity/lookup", {
        body: { thread_ids: [created.thread_id, created.thread_id, "missing"] },
      }),
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].thread.completion?.version).toBeGreaterThan(0);
  } finally {
    socket.close();
  }
});

it("rejects realtime access before exposing even heartbeat or application frames", async () => {
  const socket = new WebSocket(
    app.origin.replace("http:", "ws:") + "/api/realtime/connect",
  );
  const messages: unknown[] = [];
  socket.onmessage = (event) => messages.push(event.data);
  const closed = new Promise<number>((resolve) => {
    socket.onclose = (event) => resolve(event.code);
  });
  socket.onopen = () => socket.send(JSON.stringify({ api_key: "incorrect" }));
  expect(await closed).toBe(4401);
  expect(messages).toEqual([]);
});

it("captures an HTTP environment override without changing defaults and never falls back from an invalid profile", async () => {
  const created = await result(
    transport.client.POST("/api/threads", {
      body: { defaults: { environment_profile_id: "environment-sandbox" } },
    }),
  );
  const thread = created.thread_id;
  const before = await result(
    transport.client.GET("/api/threads/{thread_id}", {
      params: { path: { thread_id: thread } },
    }),
  );
  const receipt = await result(
    transport.client.POST("/api/threads/{thread_id}/submit", {
      params: { path: { thread_id: thread } },
      body: {
        parts: ["Use Full Control for this Run"],
        environment: { environment_profile_id: "environment-native" },
      },
    }),
  );
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
  expect(inspection.captured?.environment_profile_id).toBe(
    "environment-native",
  );
  const after = await result(
    transport.client.GET("/api/threads/{thread_id}", {
      params: { path: { thread_id: thread } },
    }),
  );
  expect(after.thread.configuration).toEqual(before.thread.configuration);
  await expect(
    transport.fetch(`/api/operations/${receipt.receipt_id}/steer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        parts: ["Continue"],
        environment_profile_id: "environment-native",
      }),
    }),
  ).rejects.toMatchObject({ status: 400 });
  await expect(
    transport.client.POST("/api/threads/{thread_id}/submit", {
      params: { path: { thread_id: thread } },
      body: {
        parts: ["Do not fall back"],
        environment: { environment_profile_id: "missing-environment" },
      },
    }),
  ).rejects.toMatchObject({ status: 400, code: "environment_profile_missing" });
  const retained = await result(
    transport.client.GET("/api/threads/{thread_id}/configuration", {
      params: { path: { thread_id: thread } },
    }),
  );
  expect(retained.captured).toEqual(inspection.captured);
  expect(retained.next_run.configuration).toEqual(before.thread.configuration);
});
