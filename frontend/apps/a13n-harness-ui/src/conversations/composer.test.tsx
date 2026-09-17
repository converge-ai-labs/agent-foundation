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
import {
  Composer,
  ComposerDrafts,
  useDraft,
  submitDraft,
  submitContinuation,
} from "./composer";
import { ConversationTranscript } from "./transcript";
import { ThreadDraft, encode, values } from "./draft";
import { TransportContext } from "../transport/context";
import { ApiError, type Schema, type Transport } from "../transport/client";

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

it.each([false, true])(
  "shows delayed shared edits as an inline icon and retains disconnect warnings (local: %s)",
  async (local) => {
    vi.useFakeTimers();
    const draft = new ThreadDraft();
    vi.spyOn(draft, "connect").mockReturnValue({ presence() {}, close() {} });
    const acknowledge = () =>
      draft.receive({
        draft_id: "draft-one",
        participant_id: "p-one",
        participants: {},
        update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
      });
    acknowledge();
    const query = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const view = render(
      <QueryClientProvider client={query}>
        <TransportContext
          value={{ client: { GET: vi.fn() } } as unknown as Transport}
        >
          <ComposerDrafts value={new Map([["thread-one", draft]])}>
            <Composer
              local={local}
              threadId="thread-one"
              activity={{ state: "inactive" }}
              canRun
              leadingControls={<span>Full Control</span>}
              profile={{ display_name: "Alice", color: "#2563eb" }}
              unauthorized={() => {}}
              reconcile={() => {}}
            />
          </ComposerDrafts>
        </TransportContext>
      </QueryClientProvider>,
    );
    try {
      const options = screen.getByText("Full Control").parentElement!;
      const slot = options.nextElementSibling;
      act(() => draft.doc.getText("text").insert(0, "Pending edit"));
      await act(() => vi.advanceTimersByTimeAsync(699));
      expect(screen.queryByRole("status")).toBeNull();
      await act(() => vi.advanceTimersByTimeAsync(1));
      if (local) {
        expect(screen.queryByRole("status")).toBeNull();
      } else {
        const status = screen.getByRole("status", { name: "Syncing edits…" });
        expect(slot?.contains(status)).toBe(true);
        expect(status.textContent).toBe("");
        expect(status.title).toBe("Syncing edits…");
        expect(status.querySelector('svg[aria-hidden="true"]')).toBeTruthy();
      }
      act(acknowledge);
      expect(screen.queryByRole("status")).toBeNull();
      expect(options.nextElementSibling).toBe(slot);
      act(() => {
        draft.status = "Disconnected";
        draft.notify();
      });
      await act(() => vi.advanceTimersByTimeAsync(700));
      if (local) expect(screen.queryByRole("status")).toBeNull();
      else
        expect(
          screen
            .getByText("Disconnected · your edits are still in this tab")
            .getAttribute("role"),
        ).toBe("status");
    } finally {
      view.unmount();
      query.clear();
      vi.useRealTimers();
    }
  },
);

it("keeps Stop available beside authored Steer, preserves input, and never cancels from a submission shortcut", async () => {
  const draft = new ThreadDraft();
  vi.spyOn(draft, "connect").mockReturnValue({ presence() {}, close() {} });
  draft.receive({
    draft_id: "draft",
    participant_id: "participant",
    participants: {},
    update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
  });
  const post = vi
    .fn()
    .mockResolvedValue({ data: { receipt_id: "current", accepted: true } });
  const get = vi.fn().mockResolvedValue({
    data: {
      receipt: { receipt_id: "current", thread_id: "one" },
      status: "running",
    },
  });
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={query}>
      <TransportContext
        value={{ client: { POST: post, GET: get } } as unknown as Transport}
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
  expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Stop" }));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/api/operations/{receipt_id}/cancel", {
      params: { path: { receipt_id: "current" } },
      signal: expect.any(AbortSignal),
    }),
  );
  await waitFor(() =>
    expect(screen.getByRole("status").textContent).toContain("Stopping"),
  );
  expect(values(draft.doc).prompt).toBe("Change direction");
  expect(
    (screen.getByRole("button", { name: "Stopping" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(
    (screen.getByRole("button", { name: "Steer" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  fireEvent.keyDown(editor, { key: "Enter" });
  expect(post).toHaveBeenCalledTimes(1);
  act(() =>
    draft.doc.getText("text").delete(0, draft.doc.getText("text").length),
  );
  expect(screen.getAllByRole("button", { name: "Stopping" })).toHaveLength(1);
  view.unmount();
  query.clear();
});

it.each([false, true])(
  "owns submission while waiting for synchronization (cancelled: %s)",
  async (cancelled) => {
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
    await act(() => submitContinuation(draft, transport, "thread-one"));
    expect(draft.submission.kind).toBe("pending");
    expect(post).not.toHaveBeenCalled();
    if (cancelled) {
      view.unmount();
      await waitFor(() => expect(draft.submission.kind).toBe("rejected"));
      acknowledge();
      expect(post).not.toHaveBeenCalled();
      expect(values(draft.doc).prompt).toBe("hello again");
      query.clear();
      return;
    }
    expect(
      (screen.getByRole("button", { name: "Submitting" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    act(acknowledge);
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][1].body.parts).toEqual(["hello again"]);
    await waitFor(() => expect(values(draft.doc).prompt).toBe(""));
    view.unmount();
    query.clear();
  },
);

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
    draft.thinking = false;
    draft.fast = false;
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
        draft.thinking = "high";
        draft.fast = true;
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
    if (action === "send")
      expect(POST.mock.calls[0][1].body.thinking).toBe(false);
    else expect(POST.mock.calls[0][1].body).not.toHaveProperty("thinking");
    if (action === "send") expect(POST.mock.calls[0][1].body.fast).toBe(false);
    else expect(POST.mock.calls[0][1].body).not.toHaveProperty("fast");
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

it("retries with an ordinary continuation without consuming the shared draft or attachments", async () => {
  const draft = new ThreadDraft();
  draft.thinking = "low";
  draft.doc.getText("text").insert(0, "Keep my next question");
  draft.addAttachment("attachment-kept");
  const before = values(draft.doc);
  let resolve!: (value: unknown) => void;
  const post = vi.fn().mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const transport = { client: { POST: post } } as unknown as Transport;
  const pending = submitContinuation(draft, transport, "thread-one");
  await submitContinuation(draft, transport, "thread-one");
  expect(post).toHaveBeenCalledTimes(1);
  expect(post).toHaveBeenCalledWith("/api/threads/{thread_id}/submit", {
    params: { path: { thread_id: "thread-one" } },
    body: {
      parts: ["Continue completing the previous task."],
      source_id: expect.any(String),
      thinking: "low",
    },
  });
  resolve({ data: { receipt_id: "receipt-new", thread_id: "thread-one" } });
  await pending;
  expect(values(draft.doc)).toEqual(before);
  expect(draft.submission).toEqual({
    kind: "accepted",
    action: "send",
    receipt: "receipt-new",
  });
  expect(draft.localInputs).toHaveLength(1);
  expect(draft.localInputs[0].state).toBe("accepted");
});

it("excludes Retry while skills load and retains uncertain acknowledgement ownership", async () => {
  const draft = new ThreadDraft();
  draft.doc.getText("text").insert(0, "$review");
  draft.receive({
    draft_id: "draft-one",
    participant_id: "participant-one",
    participants: {},
    update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
  });
  let resolve!: (catalog: Schema<"SkillCatalogView">) => void;
  const catalog = new Promise<Schema<"SkillCatalogView">>((done) => {
    resolve = done;
  });
  const post = vi.fn().mockRejectedValue(new Error("Response lost"));
  const transport = { client: { POST: post } } as unknown as Transport;
  const pending = submitDraft(
    draft,
    transport,
    "thread-one",
    "send",
    undefined,
    undefined,
    undefined,
    undefined,
    () => catalog,
  );
  expect(draft.submission.kind).toBe("pending");
  await submitContinuation(draft, transport, "thread-one");
  expect(post).not.toHaveBeenCalled();
  resolve({ catalog_id: "a".repeat(64), context_kind: "idle", items: [] });
  await pending;
  expect(draft.submission.kind).toBe("unknown");
  await submitContinuation(draft, transport, "thread-one");
  await submitDraft(draft, transport, "thread-one", "send");
  expect(post).toHaveBeenCalledTimes(1);
  expect(post.mock.calls[0][1].body.parts).toEqual(["$review"]);
  expect(draft.localInputs).toHaveLength(1);
  expect(draft.submission.kind).toBe("unknown");
  expect(values(draft.doc).prompt).toBe("$review");
});

it("owns continuation preparation and does not repeat an uncertain acknowledgement", async () => {
  const draft = new ThreadDraft();
  draft.doc.getText("text").insert(0, "Untouched");
  const post = vi.fn().mockRejectedValue(new Error("Response lost"));
  const transport = { client: { POST: post } } as unknown as Transport;
  let prepared!: () => void;
  const preparation = new Promise<void>((resolve) => {
    prepared = resolve;
  });
  const pending = submitContinuation(
    draft,
    transport,
    "thread-one",
    () => preparation,
  );
  expect(draft.submission.kind).toBe("pending");
  await submitDraft(draft, transport, "thread-one", "send");
  await submitContinuation(draft, transport, "thread-one");
  expect(post).not.toHaveBeenCalled();
  prepared();
  await pending;
  expect(draft.submission.kind).toBe("unknown");
  await submitContinuation(draft, transport, "thread-one");
  expect(post).toHaveBeenCalledTimes(1);
  expect(values(draft.doc).prompt).toBe("Untouched");
});

it.each(["restore", "other-field", "rejected"])(
  "keeps repeated sends responsive and handles preparation focus (%s)",
  async (outcome) => {
    const draft = new ThreadDraft();
    vi.spyOn(draft, "connect").mockReturnValue({ presence() {}, close() {} });
    draft.doc.getText("text").insert(0, "Follow up");
    draft.receive({
      draft_id: "draft",
      participant_id: "person",
      participants: {},
      update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
    });
    let release!: () => void;
    const wait = new Promise<void>((resolve) => {
      release = resolve;
    });
    const post = vi.fn(async () => {
      await wait;
      if (outcome === "rejected") throw new ApiError("Conversation busy", 409);
      return { data: { receipt_id: "receipt", thread_id: "thread-one" } };
    });
    const query = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={query}>
        <TransportContext
          value={{ client: { POST: post } } as unknown as Transport}
        >
          <ComposerDrafts value={new Map([["thread-one", draft]])}>
            <input aria-label="Other field" />
            <MessageStream />
            <Composer
              threadId="thread-one"
              activity={{ state: "inactive" }}
              canRun
              profile={{ display_name: "Test", color: "#000000" }}
              unauthorized={() => {}}
              reconcile={() => {}}
            />
          </ComposerDrafts>
        </TransportContext>
      </QueryClientProvider>,
    );
    const textbox = screen.getByRole("textbox", { name: "Shared prompt" });
    act(() => textbox.focus());
    fireEvent.keyDown(textbox, { key: "Enter" });
    await screen.findByRole("button", { name: "Submitting" });
    expect(screen.getByLabelText("Message stream").textContent).toBe("");
    expect(values(draft.doc).prompt).toBe("Follow up");
    // jsdom does not implement inert's native blur, so simulate it explicitly.
    act(() => textbox.blur());
    const other = screen.getByRole("textbox", { name: "Other field" });
    if (outcome === "other-field") act(() => other.focus());
    fireEvent.keyDown(textbox, { key: "Enter" });
    expect(post).toHaveBeenCalledOnce();
    await act(async () => release());
    await waitFor(() =>
      expect(draft.submission.kind).toBe(
        outcome === "rejected" ? "rejected" : "accepted",
      ),
    );
    expect(screen.getByRole("textbox", { name: "Shared prompt" })).toBe(
      textbox,
    );
    expect(document.activeElement).toBe(
      outcome === "other-field" ? other : textbox,
    );
    expect(values(draft.doc).prompt).toBe(
      outcome === "rejected" ? "Follow up" : "",
    );
    expect(screen.queryByText("Sending…")).toBeNull();
    if (outcome === "rejected") {
      expect(screen.getByText("Conversation busy")).toBeTruthy();
      expect(screen.getByLabelText("Message stream").textContent).toBe("");
    } else
      expect(screen.getByLabelText("Message stream").textContent).toContain(
        "Follow up",
      );
    cleanup();
    query.clear();
  },
);

it("explains unavailable send conditions without consuming the authored draft", () => {
  const draft = new ThreadDraft();
  draft.doc.getText("text").insert(0, "Keep my input");
  vi.spyOn(draft, "connect").mockReturnValue({ presence() {}, close() {} });
  const post = vi.fn();
  const query = new QueryClient();
  const props = {
    threadId: "thread-one",
    activity: { state: "inactive" } as Schema<"RootActivityView">,
    canRun: false,
    unavailableReason: "Updating conversation settings…",
    profile: { display_name: "Test", color: "#000000" },
    unauthorized: () => {},
    reconcile: () => {},
  };
  render(
    <QueryClientProvider client={query}>
      <TransportContext
        value={{ client: { POST: post } } as unknown as Transport}
      >
        <ComposerDrafts value={new Map([["thread-one", draft]])}>
          <Composer {...props} />
        </ComposerDrafts>
      </TransportContext>
    </QueryClientProvider>,
  );
  expect(
    screen.getByText("Waiting for the shared draft connection…"),
  ).toBeTruthy();
  const editor = screen.getByRole("textbox", { name: "Shared prompt" });
  fireEvent.keyDown(editor, { key: "Enter" });
  act(() =>
    draft.receive({
      draft_id: "draft",
      participant_id: "person",
      participants: {},
      update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
    }),
  );
  expect(screen.getByText("Updating conversation settings…")).toBeTruthy();
  fireEvent.keyDown(editor, { key: "Enter" });
  expect(post).not.toHaveBeenCalled();
  expect(values(draft.doc).prompt).toBe("Keep my input");
  cleanup();
  query.clear();
});

it("stops while steering waits for synchronization without submitting or clearing that draft", async () => {
  const draft = new ThreadDraft();
  vi.spyOn(draft, "connect").mockReturnValue({ presence() {}, close() {} });
  draft.receive({
    draft_id: "draft",
    participant_id: "person",
    participants: {},
    update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
  });
  draft.doc.getText("text").insert(0, "Keep this unsynchronized guidance");
  const post = vi
    .fn()
    .mockResolvedValue({ data: { receipt_id: "current", accepted: true } });
  const get = vi.fn().mockResolvedValue({
    data: {
      receipt: { receipt_id: "current", thread_id: "one" },
      status: "running",
    },
  });
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={query}>
      <TransportContext
        value={{ client: { POST: post, GET: get } } as unknown as Transport}
      >
        <ComposerDrafts value={new Map([["one", draft]])}>
          <Composer
            threadId="one"
            canRun
            activity={{
              state: "running",
              receipt_id: "current",
              available_actions: ["cancel", "steer"],
            }}
            profile={{ display_name: "Test", color: "#000000" }}
            unauthorized={() => {}}
            reconcile={() => {}}
          />
        </ComposerDrafts>
      </TransportContext>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Steer" }));
  expect(draft.submission.kind).toBe("pending");
  expect(post).not.toHaveBeenCalled();
  const stop = screen.getByRole("button", {
    name: "Stop",
  }) as HTMLButtonElement;
  expect(stop.disabled).toBe(false);
  fireEvent.click(stop);
  await waitFor(() => expect(draft.submission.kind).toBe("rejected"));
  expect(post).toHaveBeenCalledTimes(1);
  expect(post.mock.calls[0][0]).toBe("/api/operations/{receipt_id}/cancel");
  expect(values(draft.doc).prompt).toBe("Keep this unsynchronized guidance");
  view.unmount();
  query.clear();
});
