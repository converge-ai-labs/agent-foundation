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
import { InboxNavigation, InboxPost, UnreadInboxPosts } from "../inbox";
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
  entries,
}: {
  threadId: string;
  atBottom: boolean;
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
  const unread = useThreadInbox({
    threadId,
    enabled: true,
    canRead: true,
    reader,
    blocked: notBlocked,
    savedEntries: entries,
  });
  return (
    <div ref={reader} data-testid="reader">
      <UnreadInboxPosts.Provider value={unread}>
        <InboxPost tool={tool} />
      </UnreadInboxPosts.Provider>
    </div>
  );
}

function setup({
  threadId = "thread-example",
  atBottom = false,
  shareId,
  ids = ["call-1"],
  entries = [savedResult],
}: {
  threadId?: string;
  atBottom?: boolean;
  shareId?: string;
  ids?: string[];
  entries?: Schema<"TranscriptEntry">[];
} = {}) {
  let unread = ids;
  const mark = vi.fn();
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const transport = createTransport("test", vi.fn(), shareId);
  const fetch = vi.fn(async (request: Request) => {
    const path = new URL(request.url).pathname;
    if (path === `/api/inbox/threads/${threadId}/unread`)
      return Response.json(
        unread.map((id) => ({
          item_id: `inbox-${id}`,
          tool_call_id: id,
          has_transcript: !id.startsWith("mcp_"),
          created_at: postedAt,
        })),
      );
    if (path === `/api/inbox/threads/${threadId}/mark-read`) {
      const body = await request.json();
      mark(body);
      unread = unread.filter((id) => !body.item_ids.includes(`inbox-${id}`));
      return new Response(null, { status: 204 });
    }
    if (path === "/api/inbox")
      return Response.json({ unread_count: unread.length });
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
            entries={entries}
          />
        </TransportContext.Provider>
      </QueryClientProvider>
    </MemoryRouter>
  );
  const { rerender } = render(tree(entries));
  return {
    mark,
    fetch,
    saveTranscript: (entries: Schema<"TranscriptEntry">[]) =>
      rerender(tree(entries)),
    changedInInbox: async (ids: string[]) => {
      unread = ids;
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
  expect(fetch).not.toHaveBeenCalled();
});
