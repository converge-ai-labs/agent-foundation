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
import { Link, MemoryRouter, Route, Routes, useParams } from "react-router";
import { createTransport, type Schema } from "../transport/client";
import { TransportContext } from "../transport/context";
import { watchThread } from "./stream";
import { LiveThreadsProvider, useLiveThread } from "./live-threads";
import { useHistory, useThread } from "./queries";

vi.mock("./stream", async (original) => ({
  ...(await original<typeof import("./stream")>()),
  watchThread: vi.fn(),
}));

type Watch = {
  args: Parameters<typeof watchThread>;
  close: ReturnType<typeof vi.fn>;
};
let watches: Map<string, Watch>;
let active: string[];
let continuations: Map<string, string>;
let queries: QueryClient;
let reads: string[];
let maxConnections: number;
function thread(id: string) {
  return {
    thread_id: id,
    parent_thread_id: null,
    root_activity: { state: active.includes(id) ? "running" : "inactive" },
  } as Schema<"ThreadSummary">;
}
function Reader() {
  const { id = "" } = useParams();
  const { display } = useLiveThread(id);
  const detail = useThread(id);
  const history = useHistory(id, detail.data?.continuation_id, !!detail.data);
  return (
    <>
      <output aria-label="Live">
        {[...display.blocks.values()].map((block) => block.text).join("")}
      </output>
      <output aria-label="History">
        {history.data?.pages[0].continuation_id}
      </output>
    </>
  );
}
function mount(current = "one", ids = ["one", "two", "three", "four"]) {
  return render(
    <QueryClientProvider client={queries}>
      <TransportContext value={createTransport("fixture", () => {})}>
        <MemoryRouter initialEntries={[`/threads/${current}`]}>
          <LiveThreadsProvider>
            {ids.map((id) => (
              <Link key={id} to={`/threads/${id}`}>
                {id}
              </Link>
            ))}
            <Link to="/settings">Settings</Link>
            <Routes>
              <Route path="/threads/:id" element={<Reader />} />
              <Route path="*" element={<p>Settings page</p>} />
            </Routes>
          </LiveThreadsProvider>
        </MemoryRouter>
      </TransportContext>
    </QueryClientProvider>,
  );
}
function output(id: string, text: string) {
  const [, , display, changed] = watches.get(id)!.args;
  display.blocks.set("reply", { id: "reply", kind: "assistant", text });
  changed();
}
beforeEach(() => {
  active = ["one", "two"];
  watches = new Map();
  continuations = new Map();
  reads = [];
  maxConnections = 0;
  queries = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 15000 } },
  });
  vi.mocked(watchThread)
    .mockReset()
    .mockImplementation((...args) => {
      const close = vi.fn(() => {
        watches.delete(args[1]);
      });
      expect(watches.has(args[1])).toBe(false);
      watches.set(args[1], { args, close });
      maxConnections = Math.max(maxConnections, watches.size);
      return close;
    });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      reads.push(path);
      let value: unknown;
      if (path === "/api/threads/activity") {
        expect(url.searchParams.get("include_active")).toBe("true");
        value = {
          rows: [],
          active_rows: active.map((id) => ({ thread: thread(id) })),
          next_cursor: null,
        };
      } else {
        const id = path.split("/")[3];
        const continuation = continuations.get(id) ?? `C0-${id}`;
        value = path.endsWith("/transcript")
          ? { continuation_id: continuation, entries: [], next_cursor: null }
          : { thread: thread(id), continuation_id: continuation };
      }
      return new Response(JSON.stringify(value), {
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
});
afterEach(() => {
  cleanup();
  queries.clear();
  vi.unstubAllGlobals();
});

it("receives background output and warms history without reconnecting when switching pages", async () => {
  const view = mount();
  await waitFor(() => expect(watches.size).toBe(2));
  await waitFor(() => expect(reads).toContain("/api/threads/two/transcript"));
  const first = watches.get("one")!;
  const second = watches.get("two")!;
  act(() => output("two", "Already received in the background"));
  fireEvent.click(screen.getByRole("link", { name: "two" }));
  expect(screen.getByLabelText("Live").textContent).toBe(
    "Already received in the background",
  );
  expect(screen.getByLabelText("History").textContent).toBe("C0-two");
  fireEvent.click(screen.getByRole("link", { name: "Settings" }));
  act(() => output("one", "Continues in Settings"));
  fireEvent.click(screen.getByRole("link", { name: "one" }));
  expect(screen.getByLabelText("Live").textContent).toBe(
    "Continues in Settings",
  );
  expect(watchThread).toHaveBeenCalledTimes(2);
  expect(first.close).not.toHaveBeenCalled();
  expect(second.close).not.toHaveBeenCalled();
  view.unmount();
  expect(first.close).toHaveBeenCalledOnce();
  expect(second.close).toHaveBeenCalledOnce();
});

it("settles a background completion into saved history after closing its stream", async () => {
  mount();
  await waitFor(() => expect(watches.size).toBe(2));
  const second = watches.get("two")!;
  act(() => output("two", "Finished output"));
  active = ["one"];
  continuations.set("two", "C1-two");
  await act(async () => {
    await queries.invalidateQueries({ queryKey: ["threads"] });
    await queries.invalidateQueries({ queryKey: ["thread", "two", "detail"] });
  });
  await waitFor(() => expect(second.close).toHaveBeenCalledOnce());
  await waitFor(() =>
    expect(
      queries.getQueryData(["thread", "two", "history", "C1-two"]),
    ).toBeTruthy(),
  );
  fireEvent.click(screen.getByRole("link", { name: "two" }));
  expect(screen.getByLabelText("History").textContent).toBe("C1-two");
  expect(screen.getByLabelText("Live").textContent).toBe("Finished output");
  // No result-follow/acknowledgement or shared composer request was made.
  expect(
    vi
      .mocked(fetch)
      .mock.calls.every(([request]) => (request as Request).method === "GET"),
  ).toBe(true);
});

it("prioritizes the visible Thread within a bounded subscription budget", async () => {
  active = ["one", "two", "three", "four"];
  mount();
  await waitFor(() => expect(watches.size).toBe(3));
  expect(watches.has("four")).toBe(false);
  fireEvent.click(screen.getByRole("link", { name: "four" }));
  await waitFor(() => expect(watches.has("four")).toBe(true));
  expect(watches.size).toBe(3);
  expect(maxConnections).toBe(3);
});

it("bounds retained observations and releases evicted query observers", async () => {
  active = [];
  const ids = Array.from({ length: 10 }, (_, i) => `thread-${i}`);
  mount(ids[0], ids);
  for (const id of ids) {
    fireEvent.click(screen.getByRole("link", { name: id }));
    await waitFor(() =>
      expect(screen.getByLabelText("History").textContent).toBe(`C0-${id}`),
    );
  }
  expect(watches.size).toBe(1);
  const details = queries
    .getQueryCache()
    .findAll()
    .filter((query) => query.queryKey[2] === "detail");
  expect(details.filter((query) => query.getObserversCount() > 0)).toHaveLength(
    8,
  );
  expect(
    queries
      .getQueryCache()
      .find({ queryKey: ["thread", ids[0], "detail"] })!
      .getObserversCount(),
  ).toBe(0);
});
