// @vitest-environment jsdom
import { useState, type ReactNode } from "react";
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
function thread(id: string): Schema<"ThreadSummary"> {
  return {
    thread_id: id,
    parent_thread_id: null,
    created_at: "2026-09-01",
    updated_at: "2026-09-01",
    metadata_version: 1,
    archived: false,
    continuation_state: "selected",
    configuration: {
      version: 1,
      agent_source: { kind: "agent", id: "agent-one" },
      environment_profile_id: "environment-native",
    },
    root_activity: { state: active.includes(id) ? "running" : "inactive" },
  };
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
function mount(
  current = "one",
  ids = ["one", "two", "three", "four"],
  resource?: ReactNode,
) {
  return render(
    <QueryClientProvider client={queries}>
      <TransportContext value={createTransport("fixture", () => {})}>
        <MemoryRouter initialEntries={[`/threads/${current}`]}>
          <LiveThreadsProvider>
            {resource}
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

it("subscribes only to the focused chat and re-handshakes cleanly without losing output across switches", async () => {
  const view = mount();
  await waitFor(() => expect(watches.size).toBe(1));
  expect(watches.has("one")).toBe(true);
  expect(watches.has("two")).toBe(false);
  expect(reads).not.toContain("/api/threads/two/transcript");

  const first = watches.get("one")!;
  act(() => {
    first.args[2].cursor = "focus:one:epoch:12";
    output("one", "Initial output on one");
  });

  fireEvent.click(screen.getByRole("link", { name: "two" }));
  await waitFor(() => expect(watches.has("two")).toBe(true));
  expect(first.close).toHaveBeenCalledOnce();
  expect(watches.size).toBe(1);
  // Cursor is cleared on unsubscribe so returning performs a fresh snapshot handshake.
  expect(first.args[2].cursor).toBeUndefined();

  act(() => output("two", "Live output on two"));
  await waitFor(() =>
    expect(screen.getByLabelText("Live").textContent).toBe(
      "Live output on two",
    ),
  );
  await waitFor(() =>
    expect(screen.getByLabelText("History").textContent).toBe("C0-two"),
  );

  // Leaving for Settings closes the focus stream while retaining the warm display.
  const second = watches.get("two")!;
  fireEvent.click(screen.getByRole("link", { name: "Settings" }));
  await waitFor(() => expect(watches.size).toBe(0));
  expect(second.close).toHaveBeenCalledOnce();

  // Returning to chat one re-establishes its focus stream and preserves retained output until snapshot replacement.
  fireEvent.click(screen.getByRole("link", { name: "one" }));
  await waitFor(() => expect(watches.has("one")).toBe(true));
  expect(screen.getByLabelText("Live").textContent).toBe(
    "Initial output on one",
  );
  expect(watches.size).toBe(1);
  view.unmount();
  expect(watches.size).toBe(0);
});

it("skips intermediate background transcript fetches while running and warms final history once settled", async () => {
  mount("one", ["one", "two"]);
  await waitFor(() => expect(watches.has("one")).toBe(true));
  await waitFor(() => expect(reads).toContain("/api/threads/one/transcript"));

  // Switch away while "one" is still running in the background.
  fireEvent.click(screen.getByRole("link", { name: "two" }));
  await waitFor(() => expect(watches.has("two")).toBe(true));
  expect(watches.has("one")).toBe(false);

  // Intermediate checkpoint C1-one arrives while "one" is still running: do not fetch C1 transcript yet.
  continuations.set("one", "C1-one");
  reads.length = 0;
  await act(async () => {
    await queries.invalidateQueries({ queryKey: ["thread", "one", "detail"] });
  });
  await waitFor(() => expect(reads).toContain("/api/threads/one"));
  expect(reads).not.toContain("/api/threads/one/transcript");

  // Once "one" finishes (inactive) at C2-one, the warm observer settles C2-one into saved history.
  active = ["two"];
  continuations.set("one", "C2-one");
  await act(async () => {
    await queries.invalidateQueries({ queryKey: ["thread", "one", "detail"] });
  });
  await waitFor(() =>
    expect(
      queries.getQueryData(["thread", "one", "history", "C2-one"]),
    ).toBeTruthy(),
  );
  fireEvent.click(screen.getByRole("link", { name: "one" }));
  expect(screen.getByLabelText("History").textContent).toBe("C2-one");
  expect(
    vi
      .mocked(fetch)
      .mock.calls.every(([request]) => (request as Request).method === "GET"),
  ).toBe(true);
});

it("does not open background focus streams or warm unvisited active roots under parallel load", async () => {
  active = Array.from({ length: 20 }, (_, index) => `active-${index}`);
  mount("idle", ["idle", ...active]);
  await waitFor(() => expect(watches.size).toBe(1));
  expect(watches.has("idle")).toBe(true);
  await waitFor(() =>
    expect(reads.filter((path) => path.endsWith("/transcript"))).toHaveLength(
      1,
    ),
  );
  for (const id of active) expect(watches.has(id)).toBe(false);

  fireEvent.click(screen.getByRole("link", { name: "active-19" }));
  await waitFor(() => expect(watches.has("active-19")).toBe(true));
  expect(watches.has("idle")).toBe(false);
  act(() => output("active-19", "Handshake output on demand"));
  await waitFor(() =>
    expect(screen.getByLabelText("Live").textContent).toBe(
      "Handshake output on demand",
    ),
  );
  await waitFor(() =>
    expect(screen.getByLabelText("History").textContent).toBe("C0-active-19"),
  );
  expect(watches.size).toBe(1);
  expect(maxConnections).toBe(1);
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

function ResourceObserver() {
  useLiveThread("one", true);
  return null;
}
function OpenResource() {
  const [open, setOpen] = useState(true);
  return (
    <>
      {open && <ResourceObserver />}
      <button onClick={() => setOpen(false)}>Close resource</button>
    </>
  );
}
it("keeps an opened resource's parent on the existing stream after navigation and releases it on close", async () => {
  active = [];
  mount("one", ["one", "four"], <OpenResource />);
  await waitFor(() => expect(watches.has("one")).toBe(true));
  const original = watches.get("one")!;
  fireEvent.click(screen.getByRole("link", { name: "four" }));
  await waitFor(() => expect(watches.has("four")).toBe(true));
  expect(watches.get("one")).toBe(original);
  fireEvent.click(screen.getByRole("button", { name: "Close resource" }));
  await waitFor(() => expect(watches.has("one")).toBe(false));
  expect(watches.has("four")).toBe(true);
});
