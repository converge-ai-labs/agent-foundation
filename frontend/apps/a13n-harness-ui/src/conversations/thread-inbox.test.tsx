// @vitest-environment jsdom
import { useLayoutEffect, useRef } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { InboxNavigation, InboxPost, ThreadInboxContext } from "../inbox";
import { createTransport, type Schema } from "../transport/client";
import { TransportContext } from "../transport/context";
import { scheduleRefresh } from "./refresh";
import { useThreadInbox } from "./thread-inbox";

const postedAt = "2026-11-27T10:00:00Z";
const savedResult: Schema<"TranscriptEntry"> = {
  position: 2,
  message_kind: "request",
  timestamp: postedAt,
  parts: [
    { kind: "tool_result", tool_name: "post_to_inbox", tool_call_id: "call-1" },
  ],
};
const tool = {
  id: "inbox:call-1",
  toolCallId: "call-1",
  name: "post_to_inbox",
  input: { title: "Ready", message: "The draft is ready for review." },
  result: { ok: true },
};
const notBlocked = () => false;

function Transcript({
  threadId,
  atBottom,
  autoMarkRead,
  displayedTool,
  entries,
}: {
  threadId: string;
  atBottom: boolean;
  autoMarkRead: boolean;
  displayedTool: typeof tool | Omit<typeof tool, "result">;
  entries: Schema<"TranscriptEntry">[];
}) {
  const reader = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = reader.current!;
    Object.defineProperties(element, {
      clientHeight: { value: 400 },
      scrollHeight: { value: 900 },
    });
    const rect = new DOMRect(40, 40, 680, 400);
    element.getClientRects = () => [rect] as unknown as DOMRectList;
    element.getBoundingClientRect = () => rect;
    element.scrollTop = atBottom ? 500 : 0;
  }, [atBottom]);
  const inbox = useThreadInbox({
    threadId,
    enabled: true,
    autoMarkRead,
    canRead: true,
    reader,
    blocked: notBlocked,
    savedEntries: entries,
  });
  return (
    <div ref={reader} data-testid="reader">
      <ThreadInboxContext.Provider value={inbox}>
        <InboxPost tool={displayedTool} />
      </ThreadInboxContext.Provider>
    </div>
  );
}

function setup({
  threadId = "thread-example",
  atBottom = false,
  autoMarkRead = true,
  displayedTool = tool,
  shareId,
  ids = ["call-1"],
  entries = [savedResult],
}: {
  threadId?: string;
  atBottom?: boolean;
  autoMarkRead?: boolean;
  displayedTool?: typeof tool | Omit<typeof tool, "result">;
  shareId?: string;
  ids?: string[];
  entries?: Schema<"TranscriptEntry">[];
} = {}) {
  const posts = ids.map((id): Schema<"ThreadInboxPost"> => ({
    item_id: `inbox-${id}`,
    tool_call_id: id,
    has_transcript: !id.startsWith("mcp_"),
    created_at: postedAt,
    read_at: null,
    done_at: null,
  }));
  const unread = (post: Schema<"ThreadInboxPost">) =>
    !post.read_at && !post.done_at;
  const mark = vi.fn();
  const patch = vi.fn();
  let patchGate: Promise<boolean> | undefined;
  let markGate: Promise<void> | undefined;
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const transport = createTransport("test", vi.fn(), shareId);
  const fetch = vi.fn(async (request: Request) => {
    const path = new URL(request.url).pathname;
    if (path === `/api/inbox/threads/${threadId}`) return Response.json(posts);
    if (path === `/api/inbox/threads/${threadId}/mark-read`) {
      const body = await request.json();
      mark(body);
      await markGate;
      posts
        .filter((post) => unread(post) && body.item_ids.includes(post.item_id))
        .forEach((post) => {
          post.read_at = postedAt;
        });
      return new Response(null, { status: 204 });
    }
    if (request.method === "PATCH" && path.startsWith("/api/inbox/")) {
      const post = posts.find((post) => path.endsWith(`/${post.item_id}`))!;
      const body = await request.json();
      patch(body);
      if (patchGate && !(await patchGate))
        return Response.json(
          { error: { code: "test", message: "Could not update this post." } },
          { status: 500 },
        );
      if (body.done !== undefined) post.done_at = body.done ? postedAt : null;
      if (body.read !== undefined || body.done)
        post.read_at = body.read === false ? null : postedAt;
      return Response.json(post);
    }
    if (path === "/api/inbox")
      return Response.json({ unread_count: posts.filter(unread).length });
    throw new Error(`Unexpected request: ${path}`);
  });
  vi.stubGlobal("fetch", fetch);
  const tree = (entries: Schema<"TranscriptEntry">[]) => (
    <MemoryRouter>
      <QueryClientProvider client={queries}>
        <TransportContext.Provider value={transport}>
          {!shareId && <InboxNavigation />}
          <Transcript
            threadId={threadId}
            atBottom={atBottom}
            autoMarkRead={autoMarkRead}
            displayedTool={displayedTool}
            entries={entries}
          />
        </TransportContext.Provider>
      </QueryClientProvider>
    </MemoryRouter>
  );
  const { rerender } = render(tree(entries));
  return {
    mark,
    patch,
    fetch,
    deferMark: () => {
      let finish!: () => void;
      markGate = new Promise((resolve) => {
        finish = resolve;
      });
      return finish;
    },
    deferPatch: () => {
      let finish!: (ok: boolean) => void;
      patchGate = new Promise((resolve) => {
        finish = resolve;
      });
      return finish;
    },
    setAutoMarkRead: (enabled: boolean) => {
      autoMarkRead = enabled;
      rerender(tree(entries));
    },
    saveTranscript: (entries: Schema<"TranscriptEntry">[]) =>
      rerender(tree(entries)),
    changedInInbox: async (ids: string[]) => {
      posts.forEach((post) => {
        post.read_at = ids.includes(post.tool_call_id) ? null : postedAt;
      });
      scheduleRefresh(queries, (query) => query.queryKey[0] === "inbox", 0);
    },
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("reveals the unread dot but only clears at the focused, visible bottom", async () => {
  const focus = vi.spyOn(document, "hasFocus").mockReturnValue(true);
  const visibility = vi
    .spyOn(document, "visibilityState", "get")
    .mockReturnValue("visible");
  const { mark } = setup();
  await screen.findByRole("img", { name: "Unread" });
  expect(
    await screen.findByRole("link", { name: "Inbox, 1 unread" }),
  ).toBeTruthy();
  const reader = screen.getByTestId("reader");
  fireEvent.scroll(reader);
  expect(mark).not.toHaveBeenCalled();

  focus.mockReturnValue(false);
  reader.scrollTop = 500;
  fireEvent.scroll(reader);
  expect(mark).not.toHaveBeenCalled();

  visibility.mockReturnValue("hidden");
  focus.mockReturnValue(true);
  fireEvent.focus(window);
  expect(mark).not.toHaveBeenCalled();

  visibility.mockReturnValue("visible");
  fireEvent(document, new Event("visibilitychange"));
  fireEvent.scroll(reader);
  fireEvent.focus(window);
  await waitFor(() =>
    expect(mark).toHaveBeenCalledExactlyOnceWith({
      item_ids: ["inbox-call-1"],
    }),
  );
  await waitFor(() =>
    expect(screen.queryByRole("img", { name: "Unread" })).toBeNull(),
  );
  expect(await screen.findByRole("link", { name: "Inbox" })).toBeTruthy();
});

it("updates an open historical card after Inbox invalidation without clearing an explicit unread mark", async () => {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  const { mark, changedInInbox } = setup();
  await screen.findByRole("img", { name: "Unread" });
  await act(() => changedInInbox([]));
  await waitFor(() =>
    expect(screen.queryByRole("img", { name: "Unread" })).toBeNull(),
  );
  await act(() => changedInInbox(["call-1"]));
  await screen.findByRole("img", { name: "Unread" });
  fireEvent.scroll(screen.getByTestId("reader"));
  expect(mark).not.toHaveBeenCalled();
});

it("keeps a live post unread until its saved result reaches the transcript", async () => {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  const { mark, saveTranscript } = setup({
    atBottom: true,
    entries: [
      {
        position: 1,
        message_kind: "response",
        timestamp: "2026-11-27T09:59:00Z",
        parts: [],
      },
    ],
  });
  await screen.findByRole("img", { name: "Unread" });
  fireEvent.scroll(screen.getByTestId("reader"));
  expect(mark).not.toHaveBeenCalled();
  saveTranscript([savedResult]);
  await waitFor(() =>
    expect(mark).toHaveBeenCalledExactlyOnceWith({
      item_ids: ["inbox-call-1"],
    }),
  );
});

it("clears Bot items even when a post has no transcript card", async () => {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  const { mark } = setup({
    threadId: "thread_bot",
    atBottom: true,
    ids: ["mcp_without_transcript", "call-1"],
    entries: [],
  });
  await waitFor(() =>
    expect(mark).toHaveBeenCalledExactlyOnceWith({
      item_ids: ["inbox-mcp_without_transcript"],
    }),
  );
});

it("keeps the shared transcript card as text without querying or opening Inbox", () => {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  const { fetch } = setup({ shareId: "shared", atBottom: true });
  expect(screen.getByText("Sent to Inbox")).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Sent to Inbox" })).toBeNull();
  expect(screen.queryByRole("img", { name: "Unread" })).toBeNull();
  expect(screen.queryByRole("button", { name: /Mark as/ })).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});

it("uses the live setting to stop and resume automatic read without hiding card actions", async () => {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  const { mark, setAutoMarkRead } = setup({
    atBottom: true,
    autoMarkRead: false,
  });
  await screen.findByRole("button", { name: "Mark as read" });
  fireEvent.scroll(screen.getByTestId("reader"));
  expect(mark).not.toHaveBeenCalled();
  setAutoMarkRead(true);
  await screen.findByRole("button", { name: "Mark as unread" });
  expect(mark).toHaveBeenCalledExactlyOnceWith({ item_ids: ["inbox-call-1"] });
});

it("keeps a card manually marked unread at the focused bottom of the chat", async () => {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  const { mark, patch } = setup({ atBottom: true });
  fireEvent.click(
    await screen.findByRole("button", { name: "Mark as unread" }),
  );
  await screen.findByRole("img", { name: "Unread" });
  const reader = screen.getByTestId("reader");
  fireEvent.scroll(reader);
  fireEvent.focus(window);
  expect(
    await screen.findByRole("link", { name: "Inbox, 1 unread" }),
  ).toBeTruthy();
  expect(mark).toHaveBeenCalledTimes(1);
  expect(patch).toHaveBeenCalledExactlyOnceWith({ read: false });
});

it("lets a manual unread write win over an already submitted automatic batch", async () => {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  const { mark, patch, deferMark } = setup();
  const finishMark = deferMark();
  const read = await screen.findByRole("button", { name: "Mark as read" });
  const reader = screen.getByTestId("reader");
  reader.scrollTop = 500;
  fireEvent.scroll(reader);
  await waitFor(() => expect(mark).toHaveBeenCalledTimes(1));
  fireEvent.click(read);
  const manualUnread = await screen.findByRole<HTMLButtonElement>("button", {
    name: "Mark as unread",
  });
  fireEvent.click(manualUnread);
  await waitFor(() => expect(manualUnread.disabled).toBe(true));
  expect(patch).toHaveBeenCalledExactlyOnceWith({ read: true });
  await act(() => finishMark());
  await screen.findByRole("button", { name: "Mark as read" });
  expect(patch.mock.calls).toEqual([[{ read: true }], [{ read: false }]]);
  expect(mark).toHaveBeenCalledTimes(1);
});

it("reads, completes, and reopens a card without navigating or changing its message", async () => {
  const { patch } = setup({ autoMarkRead: false });
  fireEvent.click(await screen.findByRole("button", { name: "Mark as read" }));
  await screen.findByRole("button", { name: "Mark as unread" });
  fireEvent.click(screen.getByRole("button", { name: "Mark as done" }));
  const reopen = await screen.findByRole("button", {
    name: "Move back to Open",
  });
  expect(screen.getByText("Done")).toBeTruthy();
  expect(
    screen.queryByRole("button", { name: /Mark as (un)?read/ }),
  ).toBeNull();
  fireEvent.click(reopen);
  await screen.findByRole("button", { name: "Mark as done" });
  expect(screen.getByRole("button", { name: "Mark as unread" })).toBeTruthy();
  expect(screen.getByText("The draft is ready for review.")).toBeTruthy();
  expect(patch.mock.calls).toEqual([
    [{ read: true }],
    [{ done: true }],
    [{ done: false }],
  ]);
});

it("disables both actions in flight and reports a mutation failure inline", async () => {
  const { deferPatch } = setup();
  const finish = deferPatch();
  const read = await screen.findByRole<HTMLButtonElement>("button", {
    name: "Mark as read",
  });
  const done = screen.getByRole<HTMLButtonElement>("button", {
    name: "Mark as done",
  });
  fireEvent.click(done);
  await waitFor(() => expect(read.disabled && done.disabled).toBe(true));
  await act(() => finish(false));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Could not update this post.",
  );
  expect(read.disabled || done.disabled).toBe(false);
  expect(screen.getByRole("img", { name: "Unread" })).toBeTruthy();
});

it.each([false, true])(
  "omits controls for a post without a successful receipt (stopped=%s)",
  async (stopped) => {
    const displayedTool = { ...tool, result: undefined, stopped };
    setup({ displayedTool });
    await screen.findByRole("link", { name: "Inbox, 1 unread" });
    expect(
      screen.getByText(stopped ? "Not sent to Inbox" : "Sending to Inbox…"),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Mark as/ })).toBeNull();
  },
);
