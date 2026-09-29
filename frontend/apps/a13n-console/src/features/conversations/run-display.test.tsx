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
  type Client,
  type ThreadFrame,
} from "../../service-client";
import type { Schema } from "../../shared/api";
import { conversationQueries, invalidateConversation } from "./api";
import { useRunDisplay } from "./run-display";
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
let attempts: number[];
let thread: Schema["ThreadView"];
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
): Schema["Item"] {
  return {
    id: `item_${first}`,
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
      items: attempts.map((number) =>
        fixtureAttempt(number, { run_id: "run_one", status: "running" }),
      ),
    });
  if (path.endsWith("/threads/thread_one")) return Response.json(thread);
  return Response.json(display.run);
}

/** The Thread stream the test feeds, frame by frame, until it detaches. */
function channel() {
  const queue: ThreadFrame[] = [];
  let wake: (() => void) | undefined;
  return {
    push(...next: ThreadFrame[]) {
      queue.push(...next);
      wake?.();
    },
    async *stream(
      _workspace: string,
      _thread: string,
      options: { signal?: AbortSignal } = {},
    ): AsyncGenerator<ThreadFrame> {
      const { signal } = options;
      while (!signal?.aborted) {
        const next = queue.shift();
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

const delta = (
  attempt: number,
  sequence: number,
  text: string,
  item = "item_1-0",
): ThreadFrame => ({
  type: "delta",
  cursor: `c${attempt}-${sequence}`,
  delta: {
    run_id: "run_one",
    attempt,
    sequence,
    event: { type: "TEXT_MESSAGE_CONTENT", messageId: item, delta: text },
    item: { id: item, kind: "text_message", state: "in_progress" },
  },
});
const boundary = (attempt: number, sequence: number): ThreadFrame => ({
  type: "boundary",
  cursor: `c${attempt}-${sequence}`,
  run_id: "run_one",
  attempt,
  sequence,
});

function Readers() {
  const queries = conversationQueries(client, "workspace");
  const current = useQuery(queries.run("run_one"));
  useQuery(queries.thread("thread_one"));
  return <output data-testid="status">{current.data?.status}</output>;
}
function Live({ live }: { live: boolean }) {
  const stream = useRunDisplay("run_one", { live });
  return (
    <>
      <output data-testid="live">{stream.state}</output>
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
      <output data-testid="attempts">
        {stream.attempts.map((attempt) => attempt.number).join("|")}
      </output>
      <output data-testid="gap">{String(stream.gap)}</output>
      <output data-testid="incomplete">{String(stream.incomplete)}</output>
      <output data-testid="dropped">{stream.dropped}</output>
      <button onClick={stream.reconnect}>Reconnect</button>
    </>
  );
}
function View({ live = true, mounted = true }) {
  return (
    <QueryClientProvider client={cache}>
      <Readers />
      {mounted && <Live live={live} />}
    </QueryClientProvider>
  );
}
function pathRequests(suffix: string) {
  return requests.filter((request) =>
    new URL(request.url).pathname.endsWith(suffix),
  );
}
const text = () => screen.getByTestId("items").textContent;

beforeEach(() => {
  requests = [];
  attempts = [1];
  display = {
    run: run(),
    items: [message("Hello", "1-0", "1-1")],
    position: "1-1",
    complete: false,
    dropped: 0,
  };
  thread = fixtureThread({
    id: "thread_one",
    session_id: "session_one",
    current_run_id: "run_one",
  });
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
  client.streamThread = vi.fn(frames.stream);
});
afterEach(() => {
  cleanup();
  cache.clear();
  client.close();
});

it("continues the committed display with the Thread's later deltas", async () => {
  display.resume_after = "1720000000000-0";
  render(<View />);
  await waitFor(() => expect(text()).toBe("Hello"));
  // The stream repeats what the display already covers before what it does not.
  await act(async () => {
    frames.push(delta(1, 1, "Hello"), delta(1, 2, " world"));
  });
  await waitFor(() => expect(text()).toBe("Hello world"));
  expect(screen.getByTestId("live").textContent).toBe("connected");
  expect(screen.getByTestId("coverage").textContent).toBe("complete");
  // The display's Run is the Run every reader shares.
  expect(screen.getByTestId("status").textContent).toBe("running");
  expect(client.streamThread).toHaveBeenCalledWith(
    "workspace",
    "thread_one",
    expect.objectContaining({
      signal: expect.any(AbortSignal),
      after: "1720000000000-0",
    }),
  );
});

it("reads a Run without following its Thread when not live", async () => {
  display = { ...display, run: run({ status: "completed" }), complete: true };
  render(<View live={false} />);
  await waitFor(() => expect(text()).toBe("Hello"));
  expect(screen.getByTestId("live").textContent).toBe("closed");
  expect(client.streamThread).not.toHaveBeenCalled();
});

it("times a reloaded Run's Items from its display", async () => {
  display = {
    ...display,
    run: run({ status: "completed" }),
    items: [message("Hello", "1-0", "1-1", "completed")],
    complete: true,
    dropped: 0,
  };
  render(<View live={false} />);
  await waitFor(() =>
    expect(screen.getByTestId("times").textContent).toBe(
      "2026-09-20T10:00:01.000Z → 2026-09-20T10:00:03.000Z",
    ),
  );
});

it("closes delivery without interrupting the Run after detachment", async () => {
  const view = render(<View />);
  await waitFor(() => expect(client.streamThread).toHaveBeenCalled());
  const signal = vi.mocked(client.streamThread).mock.calls[0]![2]!.signal!;
  const count = requests.length;
  await act(async () => {
    view.rerender(<View mounted={false} />);
  });
  expect(signal.aborted).toBe(true);
  expect(requests).toHaveLength(count);
  expect(requests.every((request) => request.method === "GET")).toBe(true);
});

it("discards provisional output when the Run changes attempt", async () => {
  render(<View />);
  await waitFor(() => expect(text()).toBe("Hello"));
  await act(async () => {
    frames.push(delta(1, 2, " world"));
  });
  await waitFor(() => expect(text()).toBe("Hello world"));
  attempts = [1, 2];
  display = {
    ...display,
    items: [
      message("Hello", "1-0", "1-1", "interrupted"),
      message("Retry", "2-1"),
    ],
    position: "2-1",
  };
  await act(async () => {
    frames.push({ type: "reset", run_id: "run_one" });
  });
  await waitFor(() => expect(text()).toBe("Hello|Retry"));
  await act(async () => {
    frames.push(delta(2, 2, " again", "item_2-1"));
  });
  await waitFor(() => expect(text()).toBe("Hello|Retry again"));
});

it("reports partial history after a gap until a boundary's display covers it", async () => {
  render(<View />);
  await waitFor(() => expect(text()).toBe("Hello"));
  await act(async () => {
    frames.push({ type: "gap", run_id: "run_one" });
  });
  await waitFor(() =>
    expect(screen.getByTestId("coverage").textContent).toBe("partial"),
  );
  expect(screen.getByTestId("gap").textContent).toBe("true");
  display = {
    ...display,
    items: [message("Hello there", "1-0", "1-6")],
    position: "1-6",
  };
  await act(async () => {
    frames.push(boundary(1, 6));
  });
  await waitFor(() =>
    expect(screen.getByTestId("coverage").textContent).toBe("complete"),
  );
  expect(text()).toBe("Hello there");
  expect(screen.getByTestId("gap").textContent).toBe("false");
});

it("reports content the display omitted as incomplete", async () => {
  display = {
    ...display,
    items: [{ ...message("", "1-0"), content: { omitted: true } }],
  };
  render(<View />);
  await waitFor(() =>
    expect(screen.getByTestId("coverage").textContent).toBe("partial"),
  );
  expect(screen.getByTestId("incomplete").textContent).toBe("true");
  expect(screen.getByTestId("gap").textContent).toBe("true");
});

it("counts the Items the display dropped without calling its content incomplete", async () => {
  display = { ...display, dropped: 12 };
  render(<View />);
  await waitFor(() => expect(text()).toBe("Hello"));
  expect(screen.getByTestId("dropped").textContent).toBe("12");
  // The execution facts of dropped Items are gone with them.
  expect(screen.getByTestId("coverage").textContent).toBe("partial");
  expect(screen.getByTestId("incomplete").textContent).toBe("false");
  expect(screen.getByTestId("gap").textContent).toBe("false");
});

it("learns a new attempt's identity once before folding its deltas", async () => {
  render(<View />);
  await waitFor(() => expect(text()).toBe("Hello"));
  expect(pathRequests("/attempts")).toHaveLength(1);
  attempts = [1, 2];
  await act(async () => {
    frames.push(
      delta(2, 1, "Second", "item_2-1"),
      delta(2, 2, " try", "item_2-1"),
    );
  });
  await waitFor(() => expect(text()).toBe("Hello|Second try"));
  expect(pathRequests("/attempts")).toHaveLength(2);
  expect(screen.getByTestId("attempts").textContent).toBe("1|2");
});

const observed = (
  sequence: number,
  name: string,
  value: unknown,
): ThreadFrame => ({
  type: "delta",
  cursor: `c1-${sequence}`,
  delta: {
    run_id: "run_one",
    attempt: 1,
    sequence,
    event: { type: "CUSTOM", name, value },
    item: { id: `obs_${sequence}`, kind: "observation", state: "completed" },
  },
});

it("reads the execution from the observations the stream delivers", async () => {
  render(<View />);
  await waitFor(() => expect(text()).toBe("Hello"));
  const lifecycle = (sequence: number, type: string) =>
    observed(sequence, "a13n.harness.lifecycle", {
      event: { payload: { type, request_id: "model-request-1" } },
    });
  await act(async () => {
    frames.push(lifecycle(2, "model_request_started"));
  });
  await waitFor(() =>
    expect(screen.getByTestId("steps").textContent).toBe("llm:running"),
  );
  await act(async () => {
    frames.push(lifecycle(3, "model_request_completed"));
  });
  await waitFor(() =>
    expect(screen.getByTestId("steps").textContent).toBe("llm:completed"),
  );
});

it("waits for the next boundary's display to hold an event the stream fragmented", async () => {
  render(<View />);
  await waitFor(() => expect(text()).toBe("Hello"));
  expect(pathRequests("/items")).toHaveLength(1);
  await act(async () => {
    frames.push(observed(2, "a13n.stream.fragment", { part: 1 }));
  });
  display = {
    ...display,
    items: [
      message("Hello", "1-0", "1-1"),
      {
        id: "obs_2",
        kind: "observation",
        state: "completed",
        first_stream_id: "1-2",
        last_stream_id: "1-2",
        started_at: "2026-09-20T10:00:02.000Z",
        ended_at: "2026-09-20T10:00:02.000Z",
        content: { name: "plugin.large", value: "whole" },
      },
    ],
    position: "1-2",
  };
  await act(async () => {
    frames.push(boundary(1, 2));
  });
  await waitFor(() => expect(pathRequests("/items")).toHaveLength(2));
});

it("reconciles the sealed display once the Thread's current Run moves on", async () => {
  render(<View />);
  await waitFor(() => expect(text()).toBe("Hello"));
  thread = { ...thread, current_run_id: null, version: 5 };
  display = {
    run: run({ status: "completed", sealed_at: "2026-09-20T10:00:09.000Z" }),
    items: [message("Hello", "1-0", "1-2", "completed")],
    position: "1-2",
    complete: true,
    dropped: 0,
  };
  await act(async () => {
    frames.push({ type: "changed", version: 5 });
  });
  await waitFor(() =>
    expect(screen.getByTestId("live").textContent).toBe("closed"),
  );
  expect(screen.getByTestId("status").textContent).toBe("completed");
});

it("joins replacement reads when a Thread change cancels reconciliation", async () => {
  render(<View />);
  await waitFor(() => expect(text()).toBe("Hello"));
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  let blocked = false;
  read = async (request) => {
    if (
      !blocked &&
      new URL(request.url).pathname.endsWith("/threads/thread_one")
    ) {
      blocked = true;
      await pending;
    }
    return response(request);
  };
  thread = { ...thread, current_run_id: null, version: 5 };
  display = { ...display, run: run({ status: "completed" }), complete: true };
  await act(async () => {
    frames.push({ type: "changed", version: 5 });
  });
  await waitFor(() => expect(blocked).toBe(true));
  await act(async () => {
    await invalidateConversation(cache, "workspace", {
      threadId: "thread_one",
    });
    release();
  });
  await waitFor(() =>
    expect(screen.getByTestId("live").textContent).toBe("closed"),
  );
});

it("re-reads the display on reconnect even when cached reads remain fresh", async () => {
  client.streamThread = vi.fn(async function* (): AsyncGenerator<ThreadFrame> {
    throw new Error("Disconnected");
  });
  render(<View />);
  await waitFor(() =>
    expect(screen.getByTestId("live").textContent).toBe("disconnected"),
  );
  display = {
    ...display,
    run: run({ status: "completed" }),
    items: [message("Hello again", "1-0", "1-3", "completed")],
    position: "1-3",
    resume_after: "1720000000001-0",
    complete: true,
    dropped: 0,
  };
  fireEvent.click(screen.getByRole("button", { name: "Reconnect" }));
  await waitFor(() => expect(text()).toBe("Hello again"));
  expect(pathRequests("/items")).toHaveLength(2);
  expect(screen.getByTestId("status").textContent).toBe("completed");
  expect(client.streamThread).toHaveBeenLastCalledWith(
    "workspace",
    "thread_one",
    expect.objectContaining({ after: "1720000000001-0" }),
  );
});
