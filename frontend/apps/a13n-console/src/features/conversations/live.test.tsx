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
} from "@converge.ai/a13n";
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
    basePath: "/acme/design",
    workspace: { id: "workspace" },
    can: () => false,
  }),
}));

function response(request: Request) {
  const path = new URL(request.url).pathname;
  if (path.endsWith("/items") || path.endsWith("/pending-actions"))
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
    expect(options?.after).toBeUndefined();
    yield event("1-0", "Hello");
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
            state: "streaming",
            parent_item_id: null,
            first_stream_id: "1-0",
            last_stream_id: "1-0",
            content: {
              events: [
                {
                  event_type: "agui.text_message_content",
                  payload: { delta: "Hello" },
                },
              ],
            },
          },
        ],
        next_cursor: null,
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
