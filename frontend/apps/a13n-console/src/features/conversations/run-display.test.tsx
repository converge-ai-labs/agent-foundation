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
  ApiError,
  createClient,
  type Client,
  type SessionEvent,
} from "../../service-client";
import type { Schema } from "../../shared/api";
import { conversationQueries } from "./api";
import { useRunDisplay } from "./run-display";
import { SessionStreamProvider } from "./session-stream";
import {
  fixtureAttempt,
  fixtureRun,
  fixtureThread,
} from "./transcript/fixture";

let client: Client;
let cache: QueryClient;
let requests: Request[];
let read: (request: Request) => Promise<Response>;
let display: Schema["RunItems"];
let frames: ReturnType<typeof channel>;
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

const run = (fields: Partial<Schema["RunView"]> = {}) =>
  fixtureRun({
    id: "run_one",
    thread_id: "thread_one",
    session_id: "session_one",
    status: "running",
    sealed_at: null,
    ...fields,
  });

function message(
  text: string,
  first: string,
  last = first,
  state: Schema["ItemState"] = "in_progress",
  ordinal = 1,
): Schema["Item"] {
  return {
    id: `item_${first}`,
    ordinal,
    kind: "text_message",
    state,
    first_stream_id: first,
    last_stream_id: last,
    started_at: "2026-09-20T10:00:01.000Z",
    ended_at:
      state === "completed" || state === "failed"
        ? "2026-09-20T10:00:03.000Z"
        : null,
    content: { messageId: `item_${first}`, role: "assistant", text },
  };
}

function response(request: Request) {
  const path = new URL(request.url).pathname;
  if (path.endsWith("/items")) return Response.json(display);
  if (path.endsWith("/attempts"))
    return Response.json({
      items: [fixtureAttempt(1, { run_id: "run_one", status: "running" })],
    });
  return Response.json(display.run);
}

/** The session stream the test feeds, event by event, until it detaches. */
function channel() {
  const queue: (SessionEvent | Error)[] = [];
  let wake: (() => void) | undefined;
  return {
    push(...next: (SessionEvent | Error)[]) {
      queue.push(...next);
      wake?.();
    },
    async *stream(
      _workspace: string,
      _session: string,
      options: { signal?: AbortSignal } = {},
    ): AsyncGenerator<SessionEvent> {
      const { signal } = options;
      while (!signal?.aborted) {
        const next = queue.shift();
        if (next instanceof Error) throw next;
        if (next) {
          yield next;
          continue;
        }
        await new Promise<void>((resolve) => {
          wake = resolve;
          signal?.addEventListener("abort", () => resolve(), { once: true });
        });
      }
    },
  };
}

const live: SessionEvent = { type: "connection", state: "live" };
const threadState = (
  current: Schema["RunView"] | null,
  last: Schema["RunView"] | null = null,
): SessionEvent => ({
  type: "thread",
  data: {
    thread: fixtureThread({
      id: "thread_one",
      session_id: "session_one",
      current_run_id: current?.id ?? null,
      last_run_id: last?.id ?? null,
      version: current?.version ?? 2,
    }),
    current_run: current,
    last_run: last,
    entries: [],
  },
});
const items = (
  shown: Schema["Item"][],
  { first = 1, complete = false } = {},
): SessionEvent => ({
  type: "items",
  data: {
    thread_id: "thread_one",
    run_id: "run_one",
    first,
    items: shown,
    continuation: null,
    complete,
  },
});
const delta = (
  sequence: number,
  event: Record<string, unknown>,
  item: Schema["Item"]["id"] = "item_1-0",
  kind: Schema["Item"]["kind"] = "text_message",
): SessionEvent => ({
  type: "delta",
  data: {
    thread_id: "thread_one",
    run_id: "run_one",
    attempt: 1,
    sequence,
    event,
    item: { id: item, kind, state: "in_progress" },
  },
});
const text = (sequence: number, value: string) =>
  delta(sequence, {
    type: "TEXT_MESSAGE_CONTENT",
    messageId: "item_1-0",
    delta: value,
  });

function Readers() {
  const current = useQuery(
    conversationQueries(client, "workspace").run("run_one"),
  );
  return <output data-testid="status">{current.data?.status}</output>;
}
function Display() {
  const stream = useRunDisplay("run_one");
  return (
    <>
      <output data-testid="connected">{String(stream.connected)}</output>
      <output data-testid="items">
        {stream.items.map((item) => item.text).join("|")}
      </output>
      <output data-testid="times">
        {stream.items
          .map((item) => `${item.startedAt} → ${item.endedAt}`)
          .join("|")}
      </output>
      <output data-testid="coverage">{stream.execution.coverage}</output>
      <output data-testid="steps">
        {stream.execution.steps
          .map((step) => `${step.kind}:${step.state}`)
          .join("|")}
      </output>
      <output data-testid="gap">{String(stream.gap)}</output>
      <output data-testid="incomplete">{String(stream.incomplete)}</output>
      <output data-testid="error">
        {stream.error instanceof Error ? stream.error.message : ""}
      </output>
      <button onClick={stream.reconnect}>Reconnect</button>
      <button
        onClick={() => stream.history.loadEarlier(stream.first, () => {})}
      >
        Load earlier content
      </button>
    </>
  );
}
function View({ streamed = true, mounted = true }) {
  return (
    <QueryClientProvider client={cache}>
      <Readers />
      {mounted &&
        (streamed ? (
          <SessionStreamProvider sessionId="session_one">
            <Display />
          </SessionStreamProvider>
        ) : (
          <Display />
        ))}
    </QueryClientProvider>
  );
}
function pathRequests(suffix: string) {
  return requests.filter((request) =>
    new URL(request.url).pathname.endsWith(suffix),
  );
}
const shown = () => screen.getByTestId("items").textContent;
const flag = (name: string) => screen.getByTestId(name).textContent;
const push = (...events: (SessionEvent | Error)[]) =>
  act(async () => frames.push(...events));

beforeEach(() => {
  requests = [];
  display = {
    run: run({ status: "completed" }),
    items: [message("Saved", "1-0", "1-1", "completed")],
    complete: true,
  };
  cache = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  read = async (request) => response(request);
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      // Keep the caller's Request: a re-wrapped Request follows its source's
      // abort signal only weakly.
      const request =
        input instanceof Request && init === undefined
          ? input
          : new Request(input, init);
      requests.push(request);
      return read(request);
    },
  });
  frames = channel();
  client.streamSession = vi.fn(frames.stream);
});
afterEach(() => {
  cleanup();
  cache.clear();
  client.close();
});

it("follows an active Run's output from its session stream", async () => {
  render(<View />);
  await push(
    live,
    threadState(run({ version: 9 })),
    items([message("Hello", "1-0", "1-1")]),
    text(2, " world"),
  );
  await waitFor(() => expect(shown()).toBe("Hello world"));
  expect(flag("connected")).toBe("true");
  expect(flag("coverage")).toBe("complete");
  // The thread's state is the Run every reader shares.
  expect(flag("status")).toBe("running");
  expect(client.streamSession).toHaveBeenCalledWith(
    "workspace",
    "session_one",
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
});

it("shows a Run's final items after its thread reports the seal", async () => {
  render(<View />);
  await push(live, threadState(run()), items([message("Hello", "1-0")]));
  await waitFor(() => expect(shown()).toBe("Hello"));
  const sealed = run({ status: "completed", version: 3 });
  await push(
    threadState(null, sealed),
    items([message("Hello there", "1-0", "1-4", "completed")], {
      complete: true,
    }),
    text(5, " late"),
  );
  await waitFor(() => expect(shown()).toBe("Hello there"));
  expect(flag("status")).toBe("completed");
});

it("pauses a Run's output after a gap until its next items repair it", async () => {
  render(<View />);
  await push(live, threadState(run()), items([message("Hello", "1-0")]));
  await push({
    type: "gap",
    data: { thread_id: "thread_one", run_id: "run_one" },
  });
  await push(text(5, " lost"));
  await waitFor(() => expect(flag("gap")).toBe("true"));
  expect(shown()).toBe("Hello");
  expect(flag("coverage")).toBe("partial");
  await push(items([message("Hello again", "1-0", "1-5")]), text(6, "!"));
  await waitFor(() => expect(shown()).toBe("Hello again!"));
  expect(flag("gap")).toBe("false");
});

it("reads a Run the stream does not hold from its committed display", async () => {
  render(<View streamed={false} />);
  await waitFor(() => expect(shown()).toBe("Saved"));
  expect(flag("connected")).toBe("false");
  expect(screen.getByTestId("times").textContent).toBe(
    "2026-09-20T10:00:01.000Z → 2026-09-20T10:00:03.000Z",
  );
  expect(client.streamSession).not.toHaveBeenCalled();
});

it("reads the committed display of a Run that ended while disconnected", async () => {
  render(<View />);
  await push(live, threadState(run()), items([message("Hello", "1-0")]));
  await waitFor(() => expect(shown()).toBe("Hello"));
  const reads = pathRequests("/items").length;
  // The new connection's snapshot no longer names the Run current.
  await push(
    { type: "connection", state: "reconnecting" },
    live,
    threadState(null, run({ status: "completed", version: 3 })),
  );
  await waitFor(() => expect(shown()).toBe("Saved"));
  expect(pathRequests("/items").length).toBeGreaterThan(reads);
});

it("loads unread items only when requested, without calling them missing", async () => {
  display = {
    ...display,
    run: run(),
    items: [message("Hello", "1-4", "1-5", "in_progress", 3)],
    complete: false,
  };
  read = async (request) => {
    const query = new URL(request.url).searchParams;
    if (!query.has("before")) return response(request);
    const after = 0;
    return Response.json({
      ...display,
      items: Array.from({ length: 2 }, (_, index) =>
        message(
          `Item ${after + 1 + index}`,
          `1-${after + index}`,
          `1-${after + index}`,
          "completed",
          after + 1 + index,
        ),
      ),
    });
  };
  render(<View />);
  await push(
    live,
    threadState(run()),
    items([message("Hello", "1-4", "1-5", "in_progress", 3)], { first: 3 }),
  );
  await waitFor(() => expect(shown()).toBe("Hello"));
  expect(flag("coverage")).toBe("window");
  expect(flag("gap")).toBe("false");
  expect(pathRequests("/items")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Load earlier content" }));
  await waitFor(() => expect(shown()).toBe("Item 1|Item 2|Hello"));
  expect(flag("coverage")).toBe("complete");
  expect(flag("gap")).toBe("false");
  expect(
    pathRequests("/items").map((request) => new URL(request.url).search),
  ).toEqual(["", "?before=3&limit=200"]);
});

it("reports content the display omitted as incomplete", async () => {
  display = {
    ...display,
    items: [{ ...message("", "1-0"), content: { omitted: true } }],
  };
  render(<View streamed={false} />);
  await waitFor(() => expect(flag("incomplete")).toBe("true"));
  expect(flag("gap")).toBe("true");
  expect(flag("coverage")).toBe("partial");
});

it("reads the execution from the observations the stream delivers", async () => {
  render(<View />);
  await push(live, threadState(run()), items([message("Hello", "1-0")]));
  const lifecycle = (sequence: number, type: string) =>
    delta(
      sequence,
      {
        type: "CUSTOM",
        name: "a13n.harness.lifecycle",
        value: { event: { payload: { type, request_id: "model-request-1" } } },
      },
      `obs_${sequence}`,
      "observation",
    );
  await push(lifecycle(2, "model_request_started"));
  await waitFor(() => expect(flag("steps")).toBe("llm:running"));
  await push(lifecycle(3, "model_request_completed"));
  await waitFor(() => expect(flag("steps")).toBe("llm:completed"));
});

it("reports a refused stream and opens it again on request", async () => {
  render(<View />);
  await push(live, threadState(run()), items([message("Hello", "1-0")]));
  await push(new ApiError(403, "forbidden", "Access ended.", {}, null));
  await waitFor(() => expect(flag("error")).toBe("Access ended."));
  fireEvent.click(screen.getByRole("button", { name: "Reconnect" }));
  await waitFor(() => expect(client.streamSession).toHaveBeenCalledTimes(2));
  await push(live, threadState(run()), items([message("Hello", "1-0")]));
  await waitFor(() => expect(flag("error")).toBe(""));
});

it("stops following when the page leaves the session", async () => {
  const view = render(<View />);
  await waitFor(() => expect(client.streamSession).toHaveBeenCalled());
  const signal = vi.mocked(client.streamSession).mock.calls[0]![2]!.signal!;
  await act(async () => {
    view.rerender(<View mounted={false} />);
  });
  expect(signal.aborted).toBe(true);
  expect(requests.every((request) => request.method === "GET")).toBe(true);
});
