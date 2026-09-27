import { expect, it, vi } from "vitest";
import { AppMessage } from "./app-message";
import type { Schema, Transport } from "../transport/client";

const view: Schema<"AppView"> = {
  view_id: "view",
  connection_generation: "conn",
  root_thread_id: "root",
  reference: {
    app_id: "app",
    thread_id: "child",
    run_id: "run",
    tool_call_id: "call",
    server_id: "server",
    tool_name: "tool",
  },
};
function setup() {
  const POST = vi.fn(),
    GET = vi.fn();
  const messages = new AppMessage(
    { client: { POST, GET } } as unknown as Transport,
    view,
  );
  return { messages, POST, GET };
}
it("does not write before trusted confirmation and rejects additional requests instead of queueing", async () => {
  const { messages, POST } = setup();
  const first = messages
    .propose([{ type: "text", text: "Review this" }])
    .catch((error: Error) => error);
  expect(POST).not.toHaveBeenCalled();
  await expect(
    messages.propose([{ type: "text", text: "Second" }]),
  ).rejects.toThrow("not queued");
  messages.decline();
  expect(((await first) as Error).message).toContain("declined");
  expect(POST).not.toHaveBeenCalled();
});
it("captures the exact message and only reads its receipt after a lost confirmed write", async () => {
  const { messages, POST, GET } = setup();
  const content = [{ type: "text" as const, text: "Original message" }];
  const completion = messages.propose(content);
  content[0].text = "Changed";
  POST.mockRejectedValue(new Error("lost"));
  GET.mockRejectedValue(new Error("offline"));
  await messages.confirm();
  expect(messages.getSnapshot()?.uncertain).toContain("not resent");
  await messages.confirm();
  expect(POST).toHaveBeenCalledTimes(1);
  expect(POST.mock.calls[0][1].body.content[0].text).toBe("Original message");
  const receipt = {
    receipt_id: "receipt",
    thread_id: "root",
    submitted_at: "2026-09-27T00:00:00Z",
  };
  GET.mockResolvedValue({
    data: {
      request: messages.getSnapshot()!.request,
      view_id: "view",
      root_thread_id: "root",
      status: "accepted",
      receipt,
    },
  });
  await messages.reconcile();
  expect(await completion).toEqual(receipt);
  expect(POST).toHaveBeenCalledTimes(1);
  messages.close();
});

it.each(["accepted", "error"])(
  "ignores a stale concurrent receipt %s after a new proposal",
  async (outcome) => {
    const { messages, POST, GET } = setup();
    const first = messages.propose([{ type: "text", text: "First" }]);
    const receipt = {
      receipt_id: "receipt",
      thread_id: "root",
      submitted_at: "2026-09-27T00:00:00Z",
    };
    const accepted = {
      request: messages.getSnapshot()!.request,
      view_id: "view",
      root_thread_id: "root",
      status: "accepted",
      receipt,
    };
    POST.mockRejectedValue(new Error("lost"));
    GET.mockRejectedValue(new Error("offline"));
    await messages.confirm();
    let resolve!: (value: unknown) => void;
    let reject!: (error: Error) => void;
    GET.mockImplementationOnce(
      () =>
        new Promise((yes, no) => {
          resolve = yes;
          reject = no;
        }),
    );
    const stale = messages.reconcile();
    GET.mockResolvedValueOnce({ data: accepted });
    await messages.reconcile();
    expect(await first).toEqual(receipt);
    const second = messages
      .propose([{ type: "text", text: "Second" }])
      .catch((error: Error) => error);
    const next = messages.getSnapshot();
    if (outcome === "accepted") resolve({ data: accepted });
    else reject(new Error("stale failure"));
    await stale;
    expect(messages.getSnapshot()).toBe(next);
    messages.decline();
    expect(((await second) as Error).message).toContain("declined");
    messages.close();
  },
);
