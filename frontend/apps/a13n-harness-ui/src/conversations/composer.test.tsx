// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as Y from "yjs";
import { Composer, ComposerDrafts, useDraft, submitDraft } from "./composer";
import { ConversationTranscript } from "./transcript";
import { ThreadDraft, encode, values } from "./draft";
import { TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";

function MessageStream() {
  const draft = useDraft("thread-one");
  return (
    <section aria-label="Message stream">
      <ConversationTranscript
        entries={[]}
        blocks={[]}
        localInputs={draft.localInputs}
        threadId="thread-one"
      />
    </section>
  );
}

beforeEach(() => {
  Object.defineProperty(Range.prototype, "getClientRects", {
    configurable: true,
    value: () => [],
  });
  Object.defineProperty(Range.prototype, "getBoundingClientRect", {
    configurable: true,
    value: () => ({
      left: 0,
      top: 0,
      right: 0,
      bottom: 0,
      width: 0,
      height: 0,
    }),
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("reserves an upload before joining and resolves it after the initial draft identity arrives", async () => {
  const draft = new ThreadDraft();
  vi.spyOn(draft, "connect").mockReturnValue({
    presence: () => {},
    close: () => {},
  });
  let resolve!: (response: Response) => void;
  const request = new Promise<Response>((done) => {
    resolve = done;
  });
  const attachment = {
    attachment_id: "attachment-one",
    name: "notes.txt",
    size: 5,
    media_type: "text/plain",
  };
  const transport = {
    fetch: vi.fn().mockReturnValue(request),
    client: { GET: vi.fn().mockResolvedValue({ data: attachment }) },
  } as unknown as Transport;
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={query}>
      <TransportContext value={transport}>
        <ComposerDrafts value={new Map([["thread-one", draft]])}>
          <Composer
            threadId="thread-one"
            activity={{ state: "inactive" } as Schema<"RootActivityView">}
            canRun
            profile={{ display_name: "Alice", color: "#2563eb" }}
            unauthorized={() => {}}
            reconcile={() => {}}
          />
        </ComposerDrafts>
      </TransportContext>
    </QueryClientProvider>,
  );
  fireEvent.change(view.container.querySelector('input[type="file"]')!, {
    target: {
      files: [new File(["notes"], "notes.txt", { type: "text/plain" })],
    },
  });
  await screen.findByRole("button", {
    name: "notes.txt · not ready",
  });
  expect(
    (
      screen.getByRole("button", {
        name: "Send",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  act(() => {
    draft.receive({
      draft_id: "draft-one",
      participant_id: "participant-one",
      participants: {},
      update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
    });
    // Typing after the reserved token while upload is in flight is not replaced.
    draft.doc
      .getText("text")
      .insert(draft.doc.getText("text").length, " after");
    resolve(
      new Response(JSON.stringify(attachment), {
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
  await screen.findByRole("button", { name: "notes.txt" });
  await waitFor(() =>
    expect(values(draft.doc)).toEqual({
      prompt: " after",
      attachment_ids: ["attachment-one"],
    }),
  );
  act(() =>
    draft.receive({
      draft_id: "draft-one",
      participant_id: "participant-one",
      participants: {},
      update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
    }),
  );
  const captured = draft.capture();
  expect(captured.parts).toEqual([
    { attachment_id: "attachment-one" },
    " after",
  ]);
  captured.doc.destroy();
  query.clear();
});

it.each(["accepted", "rejected", "unknown"] as const)(
  "steers ordered attachments and preserves uncaptured edits when %s",
  async (outcome) => {
    const draft = new ThreadDraft();
    vi.spyOn(draft, "connect").mockReturnValue({
      presence: () => {},
      close: () => {},
    });
    draft.doc.getText("text").insert(0, "before after");
    draft.addAttachment("attachment-image", 7);
    draft.receive({
      draft_id: "draft-one",
      participant_id: "participant-one",
      participants: {},
      update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
    });
    const capture = draft.capture();
    const input = { parts: capture.parts };
    capture.doc.destroy();
    const attachment = {
      attachment_id: "attachment-image",
      name: "image.png",
      size: 128 * 1024,
      media_type: "image/png",
    };
    let finish!: (value: unknown) => void;
    const request = new Promise((resolve) => {
      finish = resolve;
    });
    const post = vi.fn().mockReturnValue(request);
    const transport = {
      client: {
        GET: vi.fn().mockResolvedValue({ data: attachment }),
        POST: post,
      },
      fetch: vi.fn().mockResolvedValue(new Response(new Blob())),
    } as unknown as Transport;
    const query = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const view = render(
      <QueryClientProvider client={query}>
        <TransportContext value={transport}>
          <ComposerDrafts value={new Map([["thread-one", draft]])}>
            <MessageStream />
            <Composer
              threadId="thread-one"
              canRun
              activity={
                {
                  state: "running",
                  receipt_id: "receipt-original",
                  available_actions: ["steer", "cancel"],
                } as Schema<"RootActivityView">
              }
              profile={{ display_name: "Alice", color: "#2563eb" }}
              unauthorized={() => {}}
              reconcile={() => {}}
            />
          </ComposerDrafts>
        </TransportContext>
      </QueryClientProvider>,
    );
    const button = screen.getByRole("button", {
      name: "Steer",
    }) as HTMLButtonElement;
    await waitFor(() => expect(button.disabled).toBe(false));
    fireEvent.click(button);
    expect(post).toHaveBeenCalledWith("/api/operations/{receipt_id}/steer", {
      params: { path: { receipt_id: "receipt-original" } },
      body: { ...input, source_id: draft.localInputs.at(-1)!.id },
    });
    act(() => {
      draft.doc.getText("text").insert(0, "NEXT ");
      draft.addAttachment("attachment-next", 0);
      finish(
        outcome === "unknown"
          ? {}
          : {
              data: {
                receipt_id: "receipt-original",
                accepted: outcome === "accepted",
              },
            },
      );
    });
    expect(draft.localInputs.at(-1)?.state).toBe("pending");
    await waitFor(() => expect(draft.submission.kind).toBe(outcome));
    expect(draft.localInputs.at(-1)?.state).toBe(outcome);
    if (outcome === "accepted") {
      expect(screen.queryByRole("status")).toBeNull();
      expect(
        screen.getByRole("region", { name: "Message stream" }).textContent,
      ).toContain("before");
    }
    expect(values(draft.doc)).toEqual({
      prompt: outcome === "accepted" ? "NEXT " : "NEXT before after",
      attachment_ids:
        outcome === "accepted"
          ? ["attachment-next"]
          : ["attachment-next", "attachment-image"],
    });
    expect(post).toHaveBeenCalledTimes(1);
    view.unmount();
    query.clear();
  },
);

it("keeps accepted receipts and healthy sync quiet while preserving errors and accessible keyboard hints", async () => {
  const draft = new ThreadDraft();
  vi.spyOn(draft, "connect").mockReturnValue({ presence() {}, close() {} });
  draft.receive({
    draft_id: "draft-one",
    participant_id: "p-one",
    participants: {},
    update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
  });
  draft.submission = {
    kind: "accepted",
    receipt: "receipt-hidden",
  };
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={query}>
      <TransportContext
        value={{ client: { GET: vi.fn() } } as unknown as Transport}
      >
        <ComposerDrafts value={new Map([["thread-one", draft]])}>
          <Composer
            threadId="thread-one"
            activity={{ state: "inactive" }}
            canRun
            profile={{ display_name: "Alice", color: "#2563eb" }}
            unauthorized={() => {}}
            reconcile={() => {}}
          />
        </ComposerDrafts>
      </TransportContext>
    </QueryClientProvider>,
  );
  expect(screen.queryByRole("status")).toBeNull();
  expect(
    screen
      .getByRole("textbox", { name: "Shared prompt" })
      .getAttribute("aria-description"),
  ).toContain("Enter to send; Shift+Enter for a new line");
  act(() => {
    draft.submission = { kind: "rejected", message: "Draft retained" };
    draft.notify();
  });
  expect(screen.getByRole("alert").textContent).toContain("Draft retained");
  act(() => {
    draft.submission = {
      kind: "unknown",
      action: "send",
      message: "Outcome unknown",
    };
    draft.notify();
  });
  expect(
    screen.getByRole("button", { name: "Refresh operation and history" }),
  ).toBeTruthy();
  expect(
    (screen.getByRole("button", { name: "Send" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  cleanup();
  query.clear();
});

it("uses one action for empty Stop and authored Steer, without turning the keyboard shortcut into cancellation", async () => {
  const draft = new ThreadDraft();
  vi.spyOn(draft, "connect").mockReturnValue({ presence() {}, close() {} });
  draft.receive({
    draft_id: "draft",
    participant_id: "participant",
    participants: {},
    update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
  });
  const post = vi.fn().mockResolvedValue({ data: {} });
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={query}>
      <TransportContext
        value={{ client: { POST: post } } as unknown as Transport}
      >
        <ComposerDrafts value={new Map([["one", draft]])}>
          <Composer
            threadId="one"
            canRun
            activity={{
              state: "running",
              receipt_id: "current",
              available_actions: ["steer", "cancel"],
            }}
            profile={{ display_name: "Test", color: "#000000" }}
            unauthorized={() => {}}
            reconcile={() => {}}
          />
        </ComposerDrafts>
      </TransportContext>
    </QueryClientProvider>,
  );
  const editor = screen.getByRole("textbox", { name: "Shared prompt" });
  expect(screen.getAllByRole("button", { name: "Stop" })).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
  fireEvent.keyDown(editor, { key: "Enter", ctrlKey: true });
  expect(post).not.toHaveBeenCalled();
  act(() => draft.doc.getText("text").insert(0, "Change direction"));
  expect(screen.getByRole("button", { name: "Steer" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  act(() =>
    draft.doc.getText("text").delete(0, draft.doc.getText("text").length),
  );
  fireEvent.click(screen.getByRole("button", { name: "Stop" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/api/operations/{receipt_id}/cancel", {
      params: { path: { receipt_id: "current" } },
    }),
  );
  view.unmount();
  query.clear();
});

it("keeps Send enabled during ordinary edit echoes but waits for synchronization before capturing once", async () => {
  const draft = new ThreadDraft();
  vi.spyOn(draft, "connect").mockReturnValue({
    presence: () => {},
    close: () => {},
  });
  const acknowledge = () =>
    draft.receive({
      draft_id: "draft-one",
      participant_id: "person",
      participants: {},
      update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
    });
  acknowledge();
  const post = vi.fn().mockResolvedValue({
    data: { receipt_id: "receipt-one", thread_id: "thread-one" },
  });
  const transport = { client: { POST: post } } as unknown as Transport;
  const query = new QueryClient();
  const view = render(
    <QueryClientProvider client={query}>
      <TransportContext value={transport}>
        <ComposerDrafts value={new Map([["thread-one", draft]])}>
          <Composer
            threadId="thread-one"
            activity={{ state: "inactive" } as Schema<"RootActivityView">}
            canRun
            profile={{ display_name: "Alice", color: "#000000" }}
            unauthorized={() => {}}
            reconcile={() => {}}
          />
        </ComposerDrafts>
      </TransportContext>
    </QueryClientProvider>,
  );
  act(() => draft.doc.getText("text").insert(0, "hello"));
  const button = screen.getByRole("button", {
    name: "Send",
  }) as HTMLButtonElement;
  expect(draft.synchronized).toBe(false);
  expect(button.disabled).toBe(false);
  act(acknowledge);
  act(() => draft.doc.getText("text").insert(5, " again"));
  expect(button.disabled).toBe(false);
  fireEvent.click(button);
  expect(post).not.toHaveBeenCalled();
  expect(
    (screen.getByRole("button", { name: "Preparing" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  act(acknowledge);
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0][1].body.parts).toEqual(["hello again"]);
  await waitFor(() => expect(values(draft.doc).prompt).toBe(""));
  view.unmount();
  query.clear();
});

it("uses distinct source identities for consecutive steering of one receipt and keeps previews out of shared state", async () => {
  const draft = new ThreadDraft();
  const POST = vi.fn().mockResolvedValue({
    data: { receipt_id: "same-receipt", accepted: true },
  });
  const transport = { client: { POST } } as unknown as Transport;
  for (let index = 0; index < 2; index++) {
    draft.doc.getText("text").insert(0, "same instruction");
    draft.status = "Connected";
    draft.receive({
      draft_id: "draft-one",
      participant_id: "participant-one",
      participants: {},
      update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
    });
    await submitDraft(draft, transport, "thread-one", "steer", "same-receipt");
  }
  expect(POST).toHaveBeenCalledTimes(2);
  const ids = POST.mock.calls.map((call) => call[1].body.source_id);
  expect(ids[0]).toMatch(/^input_[0-9a-f]{32}$/);
  expect(ids[1]).not.toBe(ids[0]);
  expect(draft.localInputs.map((input) => input.id)).toEqual(ids);
  expect(values(draft.doc).prompt).toBe("");
  expect(JSON.stringify(draft.doc.toJSON())).not.toContain(ids[0]);
  draft.doc.destroy();
});

it.each(["send", "steer"] as const)(
  "submits captured skill references for %s and preserves later edits",
  async (action) => {
    const draft = new ThreadDraft();
    draft.doc.getText("text").insert(0, "Use $review $review $unknown");
    draft.status = "Connected";
    draft.receive({
      draft_id: "draft-one",
      participant_id: "participant-one",
      participants: {},
      update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
    });
    const POST = vi.fn().mockResolvedValue({
      data: {
        receipt_id: "receipt",
        thread_id: "thread-one",
        accepted: true,
      },
    });
    const catalog: Schema<"SkillCatalogView"> = {
      catalog_id: "a".repeat(64),
      context_kind: "idle",
      items: [
        {
          item_id: "b".repeat(64),
          name: "review",
          description: "Review",
          source_id: "project",
          logical_path: ".agents/skills/review",
        },
      ],
    };
    await submitDraft(
      draft,
      { client: { POST } } as unknown as Transport,
      "thread-one",
      action,
      "receipt",
      undefined,
      undefined,
      undefined,
      async () => {
        draft.doc
          .getText("text")
          .insert(draft.doc.getText("text").length, " later");
        return catalog;
      },
    );
    expect(POST.mock.calls[0][1].body.skill_references).toEqual([
      {
        catalog_id: catalog.catalog_id,
        item_id: catalog.items[0].item_id,
        name: "review",
      },
    ]);
    expect(POST.mock.calls[0][1].body.parts).toEqual([
      "Use $review $review $unknown",
    ]);
    expect(values(draft.doc).prompt).toBe(" later");
  },
);

it("does not submit after navigation cancels a pending skill catalog read", async () => {
  const draft = new ThreadDraft();
  draft.doc.getText("text").insert(0, "$review");
  draft.status = "Connected";
  draft.receive({
    draft_id: "draft-one",
    participant_id: "participant-one",
    participants: {},
    update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
  });
  const POST = vi.fn();
  const abort = new AbortController();
  await submitDraft(
    draft,
    { client: { POST } } as unknown as Transport,
    "thread-one",
    "send",
    undefined,
    undefined,
    undefined,
    undefined,
    async () => {
      abort.abort();
      return { catalog_id: "a".repeat(64), context_kind: "idle", items: [] };
    },
    abort.signal,
  );
  expect(POST).not.toHaveBeenCalled();
  expect(values(draft.doc).prompt).toBe("$review");
});
