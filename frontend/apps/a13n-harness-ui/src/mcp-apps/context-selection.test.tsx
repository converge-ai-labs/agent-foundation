// @vitest-environment jsdom
import { expect, it, vi } from "vitest";
import { AppSession } from "./app-session";
import { AppContextSelection } from "./context-selection";
import { submitDraft } from "../conversations/composer";
import { ThreadDraft, encode } from "../conversations/draft";
import * as Y from "yjs";
import type { Schema, Transport } from "../transport/client";

it("captures this browser's exact selected context before preparation, only on ordinary Send", async () => {
  const view: Schema<"AppView"> = {
    view_id: "view-one",
    connection_generation: "connection-one",
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
  const context: Schema<"AppContext"> = {
    reference: { view_id: view.view_id, context_id: "first" },
    value: { structuredContent: { area: "north" } },
  };
  const PUT = vi.fn().mockResolvedValue({ data: context });
  const POST = vi.fn().mockResolvedValue({
    data: { receipt_id: "receipt", thread_id: "root", accepted: true },
  });
  const transport = { client: { PUT, POST } } as unknown as Transport;
  const session = new AppSession(transport, view);
  const browser = new AppContextSelection();
  const unregister = browser.register(session);
  await session.updateContext(context.value);
  session.selectContext(true);
  expect(new AppContextSelection().capture("root")).toEqual([]);
  expect(browser.capture("other")).toEqual([]);
  for (const action of ["send", "steer"] as const) {
    const draft = new ThreadDraft();
    draft.doc.getText("text").insert(0, "Keep ordered authored text");
    draft.status = "Connected";
    draft.receive({
      draft_id: "draft-one",
      participant_id: "participant-one",
      participants: {},
      update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
    });
    PUT.mockResolvedValue({
      data: {
        ...context,
        reference: { ...context.reference, context_id: "second" },
      },
    });
    await submitDraft(
      draft,
      transport,
      "root",
      action,
      "receipt",
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      async () => {
        await session.updateContext({ structuredContent: { area: "south" } });
      },
      () => browser.capture("root"),
    );
    const body = POST.mock.lastCall?.[1].body;
    expect(body.parts).toEqual(["Keep ordered authored text"]);
    expect(body).not.toHaveProperty("prompt");
    if (action === "send")
      expect(body.app_context).toEqual([context.reference]);
    else expect(body).not.toHaveProperty("app_context");
  }
  unregister();
  expect(browser.capture("root")).toEqual([]);
});
