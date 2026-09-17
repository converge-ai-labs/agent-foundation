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
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import {
  createClient,
  ReplayGapError,
  type Client,
  type RunEvent,
} from "../../service-client";
import {
  conversationKeys,
  conversationQueries,
  invalidateConversation,
} from "./api";
import { useLiveRun } from "./live";

let client: Client;
let cache: QueryClient;
let requests: Request[];
let read: (request: Request) => Promise<Response>;
let status = "running";
let version = 1;
vi.mock("../../auth/context", () => ({
  useClient: () => client,
  revalidateSession: vi.fn(),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/design",
    workspace: { id: "workspace" },
    can: () => false,
  }),
}));

function response(request: Request) {
  const path = new URL(request.url).pathname;
  if (path.endsWith("/items"))
    return Response.json({
      items: [],
      next_cursor: null,
      snapshot_version: version,
      projection_cursor: null,
      complete: true,
      incomplete_reason: null,
      finalized: status === "completed",
    });
  if (path.endsWith("/pending-actions"))
    return Response.json({ items: [], next_cursor: null });
  if (path.includes("/threads/"))
    return Response.json({
      id: "thread_one",
      session_id: "session_one",
      version,
    });
  return Response.json({
    id: "run_one",
    thread_id: "thread_one",
    session_id: "session_one",
    status,
    version,
  });
}
function event(cursor: string, text: string): RunEvent {
  return {
    cursor,
    event: {
      schema_version: "1",
      event_id: `event_${cursor}`,
      run_id: "run_one",
      thread_id: "thread_one",
      occurred_at: "2026-09-09T00:00:00Z",
      event_type: "agui.text_message_content",
      item_id: "item_one",
      payload: { item_kind: "text_message", delta: text },
    },
  };
}
async function* waitingStream(
  _runId: string,
  options: { signal?: AbortSignal } = {},
) {
  yield event("1-0", "Hello");
  await new Promise<void>((resolve) => {
    if (options.signal?.aborted) resolve();
    else
      options.signal?.addEventListener("abort", () => resolve(), {
        once: true,
      });
  });
}
function Readers() {
  const queries = conversationQueries(client, "workspace");
  const run = useQuery(queries.run("run_one"));
  useQuery(queries.pending("run_one"));
  useQuery(queries.thread("thread_one"));
  return <output data-testid="version">{run.data?.version}</output>;
}
function Live() {
  const live = useLiveRun("run_one");
  return (
    <>
      <output data-testid="live">{live.state}</output>
      <output data-testid="items">
        {live.items.map((item) => item.text).join("")}
      </output>
      <output data-testid="gap">{String(live.gap)}</output>
      <button onClick={live.reconnect}>Reconnect</button>
      {live.hasEarlier && (
        <button
          disabled={live.loadingEarlier}
          onClick={() => void live.loadEarlier()}
        >
          Earlier
        </button>
      )}
      <output data-testid="earlier-error">
        {live.earlierError ? "failed" : ""}
      </output>
    </>
  );
}
function View({ live = true }: { live?: boolean }) {
  return (
    <QueryClientProvider client={cache}>
      <Readers />
      {live && <Live />}
    </QueryClientProvider>
  );
}
function pathRequests(suffix: string) {
  return requests.filter((request) =>
    new URL(request.url).pathname.endsWith(suffix),
  );
}

beforeEach(() => {
  requests = [];
  status = "running";
  version = 1;
  cache = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  read = async (request) => response(request);
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const request = new Request(input, init);
      requests.push(request);
      return read(request);
    },
  });
  client.streamRun = vi.fn(waitingStream);
});
afterEach(() => {
  cleanup();
  cache.clear();
  client.close();
});

it("shares initial Run and pending-action reads between the view and live attachment", async () => {
  let finish!: () => void;
  const wait = new Promise<void>((resolve) => {
    finish = resolve;
  });
  read = async (request) => {
    await wait;
    return response(request);
  };
  render(<View />);
  await waitFor(() => expect(pathRequests("/pending-actions")).toHaveLength(1));
  expect(pathRequests("/runs/run_one")).toHaveLength(1);
  await act(async () => {
    finish();
  });
  await waitFor(() =>
    expect(screen.getByTestId("live").textContent).toBe("connected"),
  );
  expect(pathRequests("/runs/run_one")).toHaveLength(1);
  expect(pathRequests("/pending-actions")).toHaveLength(1);
});

it("keeps shared resource reads alive when only the live consumer detaches", async () => {
  let finish!: () => void;
  const wait = new Promise<void>((resolve) => {
    finish = resolve;
  });
  read = async (request) => {
    await wait;
    return response(request);
  };
  const view = render(<View />);
  await waitFor(() => expect(pathRequests("/runs/run_one")).toHaveLength(1));
  view.rerender(<View live={false} />);
  expect(pathRequests("/runs/run_one")[0]!.signal.aborted).toBe(false);
  await act(async () => {
    finish();
  });
  await waitFor(() =>
    expect(screen.getByTestId("version").textContent).toBe("1"),
  );
  expect(client.streamRun).not.toHaveBeenCalled();
  expect(requests.every((request) => request.method === "GET")).toBe(true);
});

it("closes delivery without interrupting the Run or starting recovery reads after detachment", async () => {
  const view = render(<View />);
  await waitFor(() =>
    expect(screen.getByTestId("live").textContent).toBe("connected"),
  );
  const count = requests.length;
  const signal = vi.mocked(client.streamRun).mock.calls[0]![1]!.signal!;
  await act(async () => {
    view.rerender(<View live={false} />);
  });
  expect(signal.aborted).toBe(true);
  expect(requests).toHaveLength(count);
  expect(requests.every((request) => request.method === "GET")).toBe(true);
});

it.each(["slow", "forbidden"])(
  "attaches the Run stream while the Thread read is %s",
  async (failure) => {
    let finish!: () => void;
    const wait = new Promise<void>((resolve) => {
      finish = resolve;
    });
    read = async (request) => {
      if (new URL(request.url).pathname.includes("/threads/")) {
        if (failure === "slow") await wait;
        return Response.json(
          { error: { code: "forbidden", message: "Unavailable" } },
          { status: 403 },
        );
      }
      return response(request);
    };
    render(<View />);
    await waitFor(() =>
      expect(screen.getByTestId("live").textContent).toBe("connected"),
    );
    await act(async () => {
      finish();
    });
    expect(requests.every((request) => request.method === "GET")).toBe(true);
  },
);

it("explicit reconnect reads current snapshots even when cached resources remain fresh", async () => {
  client.streamRun = vi.fn(async function* () {
    throw new Error("Disconnected");
  });
  render(<View />);
  await waitFor(() =>
    expect(screen.getByTestId("live").textContent).toBe("disconnected"),
  );
  version = 2;
  status = "completed";
  fireEvent.click(screen.getByRole("button", { name: "Reconnect" }));
  await waitFor(() =>
    expect(screen.getByTestId("live").textContent).toBe("closed"),
  );
  expect(screen.getByTestId("version").textContent).toBe("2");
  expect(pathRequests("/runs/run_one")).toHaveLength(2);
  expect(pathRequests("/pending-actions")).toHaveLength(2);
  expect(pathRequests("/items")).toHaveLength(2);
});

it("joins replacement reads when a notification cancels terminal reconciliation", async () => {
  let complete!: () => void;
  const terminal = new Promise<void>((resolve) => {
    complete = resolve;
  });
  client.streamRun = vi.fn(async function* () {
    yield event("1-0", "Hello");
    await terminal;
  });
  render(<View />);
  await waitFor(() =>
    expect(screen.getByTestId("live").textContent).toBe("connected"),
  );
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  let blocked = false;
  read = async (request) => {
    if (!blocked && new URL(request.url).pathname.endsWith("/runs/run_one")) {
      blocked = true;
      await pending;
    }
    return response(request);
  };
  status = "completed";
  version = 2;
  await act(async () => {
    complete();
  });
  await waitFor(() => expect(blocked).toBe(true));
  await act(async () => {
    await invalidateConversation(cache, "workspace", { runId: "run_one" });
    release();
  });
  await waitFor(() =>
    expect(screen.getByTestId("live").textContent).toBe("closed"),
  );
  expect(screen.getByTestId("version").textContent).toBe("2");
  await waitFor(() =>
    expect(screen.getByTestId("items").textContent).toBe("Hello"),
  );
  expect(client.streamRun).toHaveBeenCalledTimes(1);
});

it("reconciles replay gaps and deduplicates retained content before resuming delivery", async () => {
  let attachments = 0;
  client.streamRun = vi.fn(async function* (_id, options) {
    if (attachments++ === 0) {
      yield event("1-0", "Hello");
      throw new ReplayGapError(
        409,
        "run_stream_replay_gap",
        "Reconcile",
        {},
        null,
      );
    }
    expect(options?.after).toBe("1-0");
    yield event("2-0", " world");
    await new Promise<void>((resolve) =>
      options?.signal?.addEventListener("abort", () => resolve(), {
        once: true,
      }),
    );
  });
  read = async (request) => {
    if (attachments && new URL(request.url).pathname.endsWith("/items"))
      return Response.json({
        items: [
          {
            id: "item_one",
            kind: "text_message",
            state: "in_progress",
            parent_item_id: null,
            first_stream_id: "1-0",
            last_stream_id: "1-0",
            content: { text: "Hello" },
          },
        ],
        next_cursor: null,
        snapshot_version: 2,
        projection_cursor: "1-0",
        complete: true,
        incomplete_reason: null,
        finalized: false,
      });
    return response(request);
  };
  render(<View />);
  await waitFor(() =>
    expect(screen.getByTestId("items").textContent).toBe("Hello world"),
  );
  expect(screen.getByTestId("gap").textContent).toBe("true");
  expect(pathRequests("/runs/run_one")).toHaveLength(2);
  expect(
    cache.getQueryData(conversationKeys("workspace").pending("run_one")),
  ).toBeDefined();
});

it("shows incomplete finalized history without opening a raw stream", async () => {
  status = "completed";
  read = async (request) => {
    if (new URL(request.url).pathname.endsWith("/items"))
      return Response.json({
        items: [],
        next_cursor: null,
        snapshot_version: 2,
        projection_cursor: "1-0",
        complete: false,
        incomplete_reason: "source_discontinuity",
        finalized: true,
      });
    return response(request);
  };
  render(<View />);
  await waitFor(() =>
    expect(screen.getByTestId("live").textContent).toBe("closed"),
  );
  expect(screen.getByTestId("gap").textContent).toBe("true");
  expect(client.streamRun).not.toHaveBeenCalled();
});

it("waits for display finalization after a terminal Run observation", async () => {
  let snapshots = 0;
  read = async (request) => {
    if (new URL(request.url).pathname.endsWith("/items"))
      return Response.json({
        items: [],
        next_cursor: null,
        snapshot_version: ++snapshots,
        projection_cursor: "1-0",
        complete: true,
        incomplete_reason: null,
        finalized: snapshots >= 3,
      });
    return response(request);
  };
  client.streamRun = vi.fn(async function* () {
    status = "completed";
    const terminal = event("2-0", "");
    terminal.event.event_type = "run.completed";
    terminal.event.item_id = null;
    yield terminal;
  });
  render(<View />);
  await waitFor(() =>
    expect(screen.getByTestId("live").textContent).toBe("closed"),
  );
  expect(snapshots).toBe(3);
  expect(client.streamRun).toHaveBeenCalledTimes(2);
});

function displayItem(id: string, text: string, first: string, last = first) {
  return {
    id,
    kind: "text_message",
    state: "in_progress",
    parent_item_id: null,
    first_stream_id: first,
    last_stream_id: last,
    content: { text },
  };
}
function displayPage(
  items: ReturnType<typeof displayItem>[],
  next: string | null,
  cursor = "3-0",
  finalized = false,
) {
  return Response.json({
    items,
    next_cursor: next,
    snapshot_version: Number(cursor.split("-")[0]),
    projection_cursor: cursor,
    complete: true,
    incomplete_reason: null,
    finalized,
  });
}

it("loads older messages only on request and retries failures without discarding the current page", async () => {
  status = "completed";
  let fail = true;
  read = async (request) => {
    const url = new URL(request.url);
    if (!url.pathname.endsWith("/items")) return response(request);
    expect(url.searchParams.get("order")).toBe("desc");
    expect(url.searchParams.get("limit")).toBe("50");
    if (url.searchParams.get("cursor")) {
      expect(url.searchParams.get("cursor")).toBe("older");
      if (fail)
        return Response.json(
          { error: { code: "items_unavailable", message: "Retry" } },
          { status: 409 },
        );
      return displayPage(
        [displayItem("old", "old-", "1-0")],
        null,
        "4-0",
        true,
      );
    }
    return displayPage(
      [
        displayItem("new", "new", "3-0"),
        displayItem("middle", "middle-", "2-0"),
      ],
      "older",
      "3-0",
      true,
    );
  };
  render(<View />);
  await waitFor(() =>
    expect(screen.getByTestId("live").textContent).toBe("closed"),
  );
  await waitFor(() =>
    expect(screen.getByTestId("items").textContent).toBe("middle-new"),
  );
  expect(pathRequests("/items")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Earlier" }));
  await waitFor(() =>
    expect(screen.getByTestId("earlier-error").textContent).toBe("failed"),
  );
  await waitFor(() =>
    expect(screen.getByTestId("items").textContent).toBe("middle-new"),
  );
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "Earlier" }));
  await waitFor(() =>
    expect(screen.getByTestId("items").textContent).toBe("old-middle-new"),
  );
  expect(screen.queryByRole("button", { name: "Earlier" })).toBeNull();
  expect(pathRequests("/items")).toHaveLength(3);
  expect(client.streamRun).not.toHaveBeenCalled();
});

it("merges older pages without advancing the live cursor or duplicating covered deltas", async () => {
  let release!: () => void;
  const proceed = new Promise<void>((resolve) => {
    release = resolve;
  });
  let attachments = 0;
  client.streamRun = vi.fn(async function* (_id, options) {
    if (attachments++ === 0) {
      expect(options?.after).toBe("3-0");
      await proceed;
      const old = event("5-0", "duplicate");
      old.event.item_id = "old";
      yield old;
      const recent = event("6-0", "!");
      recent.event.item_id = "new";
      yield recent;
    } else {
      expect(options?.after).toBe("6-0");
      await new Promise<void>((resolve) =>
        options?.signal?.addEventListener("abort", () => resolve(), {
          once: true,
        }),
      );
    }
  });
  read = async (request) => {
    const url = new URL(request.url);
    if (!url.pathname.endsWith("/items")) return response(request);
    if (url.searchParams.has("cursor"))
      return displayPage(
        [displayItem("old", "full-old-", "1-0", "10-0")],
        null,
        "10-0",
      );
    return displayPage([displayItem("new", "new", "3-0")], "older");
  };
  render(<View />);
  await waitFor(() => expect(client.streamRun).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", { name: "Earlier" }));
  await waitFor(() =>
    expect(screen.getByTestId("items").textContent).toBe("full-old-new"),
  );
  expect(client.streamRun).toHaveBeenCalledTimes(1);
  await act(async () => {
    release();
  });
  await waitFor(() => expect(client.streamRun).toHaveBeenCalledTimes(2));
  expect(screen.getByTestId("items").textContent).toBe("full-old-new!");
});

it("hides unloaded Item fragments while admitting new Items from the live stream", async () => {
  client.streamRun = vi.fn(async function* (_id, options) {
    const hidden = event("4-0", "orphaned suffix");
    hidden.event.item_id = "old";
    yield hidden;
    const start = event("5-0", "");
    start.event.item_id = "fresh";
    start.event.event_type = "agui.text_message_start";
    yield start;
    const content = event("6-0", "fresh");
    content.event.item_id = "fresh";
    yield content;
    await new Promise<void>((resolve) =>
      options?.signal?.addEventListener("abort", () => resolve(), {
        once: true,
      }),
    );
  });
  read = async (request) => {
    if (new URL(request.url).pathname.endsWith("/items"))
      return displayPage([displayItem("new", "new-", "3-0")], "older");
    return response(request);
  };
  render(<View />);
  await waitFor(() =>
    expect(screen.getByTestId("items").textContent).toBe("new-fresh"),
  );
  expect(pathRequests("/items")).toHaveLength(1);
});

it("cancels an in-flight earlier page when reconnecting to a new window", async () => {
  status = "completed";
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  let oldRequest: Request | undefined;
  read = async (request) => {
    const url = new URL(request.url);
    if (!url.pathname.endsWith("/items")) return response(request);
    if (url.searchParams.has("cursor")) {
      oldRequest = request;
      await pending;
      return displayPage(
        [displayItem("old", "obsolete", "1-0")],
        null,
        "3-0",
        true,
      );
    }
    return displayPage(
      [displayItem("new", "new", "3-0")],
      "older",
      "3-0",
      true,
    );
  };
  render(<View />);
  await waitFor(() =>
    expect(screen.getByTestId("live").textContent).toBe("closed"),
  );
  fireEvent.click(screen.getByRole("button", { name: "Earlier" }));
  await waitFor(() => expect(oldRequest).toBeDefined());
  fireEvent.click(screen.getByRole("button", { name: "Reconnect" }));
  await waitFor(() => expect(oldRequest!.signal.aborted).toBe(true));
  await act(async () => {
    release();
  });
  await waitFor(() =>
    expect(screen.getByTestId("items").textContent).toBe("new"),
  );
});
