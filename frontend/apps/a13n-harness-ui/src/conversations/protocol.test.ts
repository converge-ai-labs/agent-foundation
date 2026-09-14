import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { once } from "node:events";
import { afterAll, beforeAll, expect, it, vi } from "vitest";
import * as Y from "yjs";
import { createTransport, result, type Transport } from "../transport/client";
import { ThreadDraft, values } from "./draft";
import { submitDraft } from "./composer";
import { FocusDisplay, watchThread } from "./stream";

let server: ReturnType<typeof spawn>;
let transport: Transport;
let stderr = "";
beforeAll(async () => {
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
    ],
    { stdio: ["pipe", "pipe", "pipe"] },
  );
  server.stderr!.on("data", (chunk) => {
    stderr += chunk;
  });
  const lines = createInterface({ input: server.stdout! });
  const origin = await new Promise<string>((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error(`Protocol listener timed out: ${stderr}`)),
      30000,
    );
    server.once("exit", (code) => {
      clearTimeout(timer);
      reject(new Error(`Protocol listener exited (${code}): ${stderr}`));
    });
    lines.on("line", (line) => {
      if (line.startsWith("{")) {
        clearTimeout(timer);
        resolve(JSON.parse(line).origin);
      }
    });
  });
  vi.stubGlobal("window", { location: { origin } });
  transport = createTransport("test-only-key", () => {
    throw new Error("Unexpected authentication failure");
  });
  const status = await result(transport.client.GET("/api/status"));
  if (status.app?.candidate_error_message)
    throw new Error(status.app.candidate_error_message);
}, 40000);
afterAll(async () => {
  transport?.close();
  if (server?.exitCode === null) {
    const exited = once(server, "exit");
    server.stdin!.end("stop\n");
    const kill = setTimeout(() => server.kill("SIGKILL"), 15000);
    await exited;
    clearTimeout(kill);
  }
  vi.unstubAllGlobals();
}, 20000);
async function until(predicate: () => boolean) {
  await vi.waitFor(() => expect(predicate()).toBe(true), {
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
  const watch = watchThread(
    transport,
    thread,
    display,
    () => {},
    () => {},
    () => {},
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
    a.doc.getMap("attachments").set("upload", attachment.attachment_id);
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
    const captured = a.capture();
    const receipt = await result(
      transport.client.POST("/api/threads/{thread_id}/submit", {
        params: { path: { thread_id: thread } },
        body: captured.input,
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
    await until(() =>
      [...display.blocks.values()].some((block) =>
        block.text.includes("Protocol response"),
      ),
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
    const history = await result(
      transport.client.GET("/api/threads/{thread_id}/transcript", {
        params: { path: { thread_id: thread } },
      }),
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
