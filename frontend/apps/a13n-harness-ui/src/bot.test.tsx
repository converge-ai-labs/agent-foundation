// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Link, MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BotComposerProvider, BotPage, BOT_THREAD_ID } from "./bot";
import { createTransport, type Schema } from "./transport/client";
import { TransportContext } from "./transport/context";
import { ComposerDrafts } from "./conversations/composer";
import { ThreadDraft } from "./conversations/draft";

vi.mock("./conversations/live-threads", async () => {
  const { FocusDisplay } = await import("./conversations/stream");
  const snapshot = {
    display: new FocusDisplay(),
    connection: "Live",
    reconnections: 0,
    revision: 0,
  };
  return { useLiveThread: () => snapshot };
});

const tracker = vi.hoisted(() => ({
  follow: vi.fn(async () => {}),
  followVersion: vi.fn(async () => {}),
  acknowledge: vi.fn(async () => {}),
  invalidate: vi.fn(),
}));
vi.mock("./conversations/results", () => ({
  useResults: () => ({
    tracker,
    followed: new Map([[BOT_THREAD_ID, 0]]),
    threads: new Map(),
  }),
}));

let snapshot: Schema<"BotSnapshot">;
let requests: Schema<"BotSubmit">[];
let reject: boolean;
let queries: QueryClient;
let transport: ReturnType<typeof createTransport>;
let draft: ThreadDraft;
const profile = { display_name: "Tester", color: "#666666" };
function typeMessage(text: string) {
  act(() => {
    const input = draft.doc.getText("text");
    input.delete(0, input.length);
    input.insert(0, text);
  });
}
beforeEach(() => {
  vi.clearAllMocks();
  draft = new ThreadDraft();
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  Object.defineProperty(Range.prototype, "getClientRects", {
    configurable: true,
    value: () => [],
  });
  Object.defineProperty(Range.prototype, "getBoundingClientRect", {
    configurable: true,
    value: () => new DOMRect(),
  });
  snapshot = {
    thread_id: BOT_THREAD_ID,
    state: { activated: false, paused: false },
    available: true,
    heartbeat_enabled: true,
    interval_seconds: 3600,
    messages: [],
    inputs: [],
  };
  requests = [];
  reject = false;
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path === "/api/bot/inputs") {
        requests.push(await request.json());
        if (reject) throw new TypeError("Offline");
        return Response.json({
          input_id: requests.at(-1)!.input_id,
          state: "queued",
        });
      }
      if (path === "/api/bot/pause") {
        snapshot = {
          ...snapshot,
          state: { ...snapshot.state, ...(await request.json()) },
        };
      }
      if (path === `/api/threads/${BOT_THREAD_ID}`)
        return Response.json({
          thread: {
            root_activity: { state: "inactive" },
            configuration: {
              version: 1,
              agent_source: { kind: "bot", id: "bot" },
              local_roots: [],
            },
            ...snapshot.thread,
          },
          available_actions: [],
          deferred_requests: [],
        });
      if (path === "/api/selectors")
        return Response.json({ agents: [], models: [] });
      if (path === "/api/bot") return Response.json(snapshot);
      return Response.json(
        { message: "Not provided by this fixture" },
        { status: 404 },
      );
    }),
  );
  queries = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  transport = createTransport("test", () => {});
});
afterEach(() => {
  cleanup();
  transport.close();
  draft.doc.destroy();
  queries.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
function mount() {
  return render(
    <TransportContext value={transport}>
      <QueryClientProvider client={queries}>
        <ComposerDrafts value={new Map([[BOT_THREAD_ID, draft]])}>
          <MemoryRouter initialEntries={["/bot"]}>
            <BotComposerProvider>
              <Link to="/elsewhere">Leave</Link>
              <Link to="/bot">Return</Link>
              <Routes>
                <Route
                  path="/bot"
                  element={
                    <BotPage profile={profile} unauthorized={() => {}} />
                  }
                />
                <Route path="/elsewhere" element={<p>Elsewhere</p>} />
              </Routes>
            </BotComposerProvider>
          </MemoryRouter>
        </ComposerDrafts>
      </QueryClientProvider>
    </TransportContext>,
  );
}
it("registers the lazy root's zero baseline before its first admission and clears accepted input", async () => {
  mount();
  await screen.findByText("Ready when you are");
  typeMessage("Track this");
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(tracker.followVersion).toHaveBeenCalledWith(BOT_THREAD_ID, 0);
  expect(tracker.followVersion.mock.invocationCallOrder[0]).toBeLessThan(
    vi.mocked(fetch).mock.invocationCallOrder.at(-1)!,
  );
  await waitFor(() => expect(draft.doc.getText("text").toString()).toBe(""));
});
it("queues human input while busy or paused without using Thread run or steer", async () => {
  snapshot = {
    ...snapshot,
    state: { activated: true, paused: true },
    operation: {
      receipt: {
        receipt_id: "receipt-existing",
        thread_id: BOT_THREAD_ID,
        submitted_at: "2026-11-10T00:00:00Z",
      },
      status: "running",
    },
  };
  mount();
  await screen.findByText("Automation paused · You can still chat");
  typeMessage("Queue this work");
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(requests).toHaveLength(1));
  const paths = vi
    .mocked(fetch)
    .mock.calls.map(([request]) => new URL((request as Request).url).pathname);
  expect(paths.some((path) => /\/(submit|steer)$/.test(path))).toBe(false);
});

it("retains drafts and uncertain exact commands across navigation without automatic retry", async () => {
  reject = true;
  mount();
  await screen.findByText("Ready when you are");
  typeMessage("Do once");
  fireEvent.click(screen.getByText("Leave"));
  fireEvent.click(screen.getByText("Return"));
  expect(screen.getByRole("textbox", { name: "Message" }).textContent).toBe(
    "Do once",
  );
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByRole("button", { name: "Retry" });
  expect(requests).toHaveLength(1);
  fireEvent.click(screen.getByText("Leave"));
  fireEvent.click(screen.getByText("Return"));
  expect(
    screen.getByRole("textbox", { name: "Message" }).closest("[inert]"),
  ).not.toBeNull();
  reject = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1]).toEqual(requests[0]);
});
it("acknowledges the rendered completion, preserves a scrolled-up reader, and exposes human decisions", async () => {
  snapshot = {
    ...snapshot,
    thread: {
      thread_id: BOT_THREAD_ID,
      completion: { version: 3 },
    } as Schema<"ThreadSummary">,
    messages: [
      {
        message_id: "m1",
        role: "assistant",
        text: "Verified result",
        created_at: "2026-11-10T00:00:00Z",
      },
    ],
  };
  const view = mount();
  await screen.findByText("Verified result");
  const visibleReader = view.container.querySelector<HTMLElement>(
    "[class*='reading_']",
  )!;
  Object.defineProperty(visibleReader, "clientHeight", {
    configurable: true,
    value: 200,
  });
  vi.spyOn(visibleReader, "getClientRects").mockReturnValue([
    new DOMRect(0, 0, 300, 200),
  ] as unknown as DOMRectList);
  vi.spyOn(visibleReader, "getBoundingClientRect").mockReturnValue(
    new DOMRect(0, 0, 300, 200),
  );
  fireEvent.scroll(visibleReader);
  await waitFor(() =>
    expect(tracker.acknowledge).toHaveBeenCalledWith(BOT_THREAD_ID, 3),
  );
  tracker.acknowledge.mockClear();
  const reader = view.container.querySelector<HTMLElement>(
    "[class*='reading_']",
  )!;
  Object.defineProperties(reader, {
    scrollHeight: { configurable: true, value: 1000 },
    clientHeight: { configurable: true, value: 200 },
    scrollTop: { configurable: true, value: 100, writable: true },
  });
  fireEvent.wheel(reader, { deltaY: -1 });
  fireEvent.scroll(reader);
  snapshot = {
    ...snapshot,
    pending_decision: true,
    thread: {
      ...snapshot.thread!,
      completion: { version: 4 },
    } as Schema<"ThreadSummary">,
  };
  await act(async () => {
    queries.setQueryData(["bot"], snapshot);
  });
  expect(reader.scrollTop).toBe(100);
  expect(tracker.acknowledge).not.toHaveBeenCalled();
  expect(
    (
      await screen.findByRole("link", { name: "Open Activity to respond" })
    ).getAttribute("href"),
  ).toBe(`/threads/${BOT_THREAD_ID}`);
  expect(
    (screen.getByRole("button", { name: "Send" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  reader.scrollTop = 800;
  fireEvent.scroll(reader);
  expect(tracker.acknowledge).toHaveBeenCalledWith(BOT_THREAD_ID, 4);
});
