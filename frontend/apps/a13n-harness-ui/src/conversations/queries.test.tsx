// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";
import { seedThreadSnapshot, useHistory, useTurnHistory } from "./queries";

afterEach(cleanup);
it("retains successful history and its identity across replacement loading and error, never across Threads", async () => {
  const page = (id: string) => ({
    continuation_id: id,
    entries: [{ position: 0, text: id }],
    next_cursor: "older",
  });
  let reject!: (reason: Error) => void;
  const GET = vi
    .fn()
    .mockResolvedValueOnce({ data: page("C0") })
    .mockImplementationOnce(
      () =>
        new Promise((_, fail) => {
          reject = fail;
        }),
    )
    .mockResolvedValueOnce({ data: page("C1") })
    .mockImplementation(() => new Promise(() => {}));
  const transport = { client: { GET } } as unknown as Transport;
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <TransportContext value={transport}>{children}</TransportContext>
    </QueryClientProvider>
  );
  const hook = renderHook(
    ({ thread, continuation }) => useHistory(thread, continuation, true),
    {
      initialProps: { thread: "one", continuation: "C0" },
      wrapper,
    },
  );
  await waitFor(() =>
    expect(hook.result.current.data?.pages[0].continuation_id).toBe("C0"),
  );
  hook.rerender({ thread: "one", continuation: "C1" });
  expect(hook.result.current.data?.pages[0].continuation_id).toBe("C0");
  expect(hook.result.current.hasNextPage).toBe(false);
  await act(async () => reject(new Error("History temporarily unavailable")));
  await waitFor(() => expect(hook.result.current.isError).toBe(true));
  expect(hook.result.current.data?.pages[0].continuation_id).toBe("C0");
  await act(async () => {
    await hook.result.current.refetch();
  });
  await waitFor(() =>
    expect(hook.result.current.data?.pages[0].continuation_id).toBe("C1"),
  );
  hook.rerender({ thread: "two", continuation: "C1" });
  expect(hook.result.current.data).toBeUndefined();
  hook.unmount();
  queryClient.clear();
});

it("uses warmed history on first mount while a newer continuation loads", () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  queryClient.setQueryData(["thread", "one", "history", "C0"], {
    pages: [{ continuation_id: "C0", entries: [], next_cursor: null }],
    pageParams: [undefined],
  });
  const transport = {
    client: { GET: vi.fn(() => new Promise(() => {})) },
  } as unknown as Transport;
  const hook = renderHook(() => useHistory("one", "C1", true), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>
        <TransportContext value={transport}>{children}</TransportContext>
      </QueryClientProvider>
    ),
  });
  expect(hook.result.current.data?.pages[0].continuation_id).toBe("C0");
  expect(hook.result.current.isPreviousHistory).toBe(true);
  expect(hook.result.current.hasNextPage).toBe(false);
  hook.unmount();
  queryClient.clear();
});

it("bootstraps initial detail and operation from a snapshot and fences a slower initial HTTP read", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const key = ["thread", "one", "detail"];
  let resolve!: (value: unknown) => void;
  let signal!: AbortSignal;
  const read = client
    .fetchQuery({
      queryKey: key,
      queryFn: (context) => {
        signal = context.signal;
        return new Promise((done) => {
          resolve = done;
        });
      },
    })
    .catch(() => undefined);
  const snapshot = {
    thread: {
      thread: { thread_id: "one", root_activity: { state: "running" } },
    },
    root_operation: {
      receipt: { receipt_id: "receipt-one" },
      status: "running",
    },
  } as Schema<"ThreadFocusSnapshot">;
  seedThreadSnapshot(client, "one", snapshot);
  expect(signal.aborted).toBe(true);
  expect(client.getQueryData(key)).toEqual(snapshot.thread);
  expect(
    client.getQueryData(["thread", "one", "operation", "receipt-one"]),
  ).toEqual(snapshot.root_operation);
  resolve({
    thread: { thread_id: "one", root_activity: { state: "inactive" } },
  });
  await read;
  expect(client.getQueryData(key)).toEqual(snapshot.thread);
  const current = { thread: { thread_id: "one", title: "Newer observation" } };
  client.setQueryData(key, current);
  seedThreadSnapshot(client, "one", snapshot);
  expect(client.getQueryData(key)).toEqual(current);
  seedThreadSnapshot(client, "other", snapshot);
  expect(client.getQueryData(["thread", "other", "detail"])).toBeUndefined();
  client.clear();
});

it("reuses old immutable history pages on remount instead of refetching the loaded window", async () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const key = ["thread", "one", "history", "C0"];
  queryClient.setQueryData(
    key,
    {
      pages: [
        { continuation_id: "C0", entries: [], next_cursor: "older" },
        { continuation_id: "C0", entries: [], next_cursor: null },
      ],
      pageParams: [undefined, "older"],
    },
    { updatedAt: Date.now() - 60_000 },
  );
  const GET = vi.fn();
  const hook = renderHook(() => useHistory("one", "C0", true), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>
        <TransportContext value={{ client: { GET } } as unknown as Transport}>
          {children}
        </TransportContext>
      </QueryClientProvider>
    ),
  });
  expect(hook.result.current.data?.pages).toHaveLength(2);
  await act(async () => {});
  expect(GET).not.toHaveBeenCalled();
  hook.unmount();
  queryClient.clear();
});

const turn: Schema<"TranscriptTurn"> = {
  turn_id: "turn-one",
  input_position: 2,
  end_position: 6,
  final_position: 5,
  preview: "Task",
  tool_count: 0,
  steering_count: 0,
};
const turnEntry = (position: number): Schema<"TranscriptEntry"> => ({
  position,
  message_kind: "response",
  parts: [{ kind: "assistant", text: `Message ${position}` }],
});
function turnHistoryHarness(GET: ReturnType<typeof vi.fn>) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const transport = { client: { GET } } as unknown as Transport;
  return {
    queryClient,
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <TransportContext value={transport}>{children}</TransportContext>
      </QueryClientProvider>
    ),
  };
}

it("publishes a whole turn once, clips adjacent turns, and reuses its continuation-bound cache", async () => {
  let finish!: (value: unknown) => void;
  const GET = vi
    .fn()
    .mockResolvedValueOnce({
      data: {
        entries: [turnEntry(4), turnEntry(5)],
        boundary_entries: [turnEntry(2)],
        next_cursor: "older",
      },
    })
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
  const { queryClient, wrapper } = turnHistoryHarness(GET);
  const hook = renderHook(() => useTurnHistory("one", "C0", turn, true), {
    wrapper,
  });
  await waitFor(() => expect(GET).toHaveBeenCalledTimes(2));
  expect(hook.result.current.data).toBeUndefined();
  expect(hook.result.current.isFetching).toBe(true);
  expect(GET.mock.calls[1][1].params.query).toEqual({
    expected_continuation_id: "C0",
    turn_id: "turn-one",
    cursor: "older",
    limit: 100,
  });
  await act(async () =>
    finish({
      data: {
        entries: [0, 1, 2, 3].map(turnEntry),
        boundary_entries: [turnEntry(5)],
        next_cursor: "previous-turn",
      },
    }),
  );
  await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
  expect(hook.result.current.data?.map((entry) => entry.position)).toEqual([
    2, 3, 4, 5,
  ]);
  hook.unmount();
  const cached = renderHook(() => useTurnHistory("one", "C0", turn, true), {
    wrapper,
  });
  await act(async () => {});
  expect(cached.result.current.isSuccess).toBe(true);
  expect(GET).toHaveBeenCalledTimes(2);
  cached.unmount();
  queryClient.clear();
});

it.each(["empty", "stalled", "missing"])(
  "rejects %s turn pages without publishing partial results or looping",
  async (kind) => {
    const GET = vi
      .fn()
      .mockResolvedValueOnce({
        data: { entries: [turnEntry(4), turnEntry(5)], next_cursor: "older" },
      })
      .mockResolvedValue({
        data: {
          entries:
            kind === "empty"
              ? []
              : kind === "stalled"
                ? [turnEntry(4), turnEntry(5)]
                : [turnEntry(2)],
          next_cursor: "older",
        },
      });
    const { queryClient, wrapper } = turnHistoryHarness(GET);
    const hook = renderHook(() => useTurnHistory("one", "C0", turn, true), {
      wrapper,
    });
    await waitFor(() => expect(hook.result.current.isError).toBe(true));
    expect(hook.result.current.error?.message).toBe(
      "Turn history is incomplete.",
    );
    expect(hook.result.current.data).toBeUndefined();
    expect(GET).toHaveBeenCalledTimes(2);
    hook.unmount();
    queryClient.clear();
  },
);

it("cancels an old continuation's whole-turn load and never mixes its late response into the new turn", async () => {
  let finishOld!: (value: unknown) => void;
  let oldSignal!: AbortSignal;
  const GET = vi
    .fn()
    .mockResolvedValueOnce({
      data: { entries: [turnEntry(4), turnEntry(5)], next_cursor: "older" },
    })
    .mockImplementationOnce((_path, options) => {
      oldSignal = options.signal;
      return new Promise((resolve) => {
        finishOld = resolve;
      });
    })
    .mockResolvedValue({
      data: { entries: [2, 3, 4, 5].map(turnEntry), next_cursor: null },
    });
  const { queryClient, wrapper } = turnHistoryHarness(GET);
  const hook = renderHook(
    ({ continuation }) => useTurnHistory("one", continuation, turn, true),
    {
      wrapper,
      initialProps: { continuation: "C0" },
    },
  );
  await waitFor(() => expect(GET).toHaveBeenCalledTimes(2));
  hook.rerender({ continuation: "C1" });
  expect(oldSignal.aborted).toBe(true);
  await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
  const current = hook.result.current.data;
  await act(async () =>
    finishOld({
      data: { entries: [turnEntry(2), turnEntry(3)], next_cursor: null },
    }),
  );
  expect(hook.result.current.data).toBe(current);
  expect(
    queryClient.getQueryData([
      "thread",
      "one",
      "turn-history",
      "C0",
      turn.turn_id,
    ]),
  ).toBeUndefined();
  hook.unmount();
  queryClient.clear();
});

it("does not read a turn already covered by the conversation window", async () => {
  const GET = vi.fn();
  const { queryClient, wrapper } = turnHistoryHarness(GET);
  const hook = renderHook(() => useTurnHistory("one", "C0", turn, false), {
    wrapper,
  });
  await act(async () => {});
  expect(GET).not.toHaveBeenCalled();
  hook.unmount();
  queryClient.clear();
});

it("keeps the previous continuation until replacement turns are complete, including failure and retry", async () => {
  const original = {
    continuation_id: "C0",
    entries: [2, 3, 4, 5].map(turnEntry),
    turns: [turn],
    next_cursor: null,
  };
  const replacement = {
    continuation_id: "C1",
    entries: [turnEntry(5)],
    boundary_entries: [turnEntry(2)],
    turns: [turn],
    next_cursor: "older",
    earlier_turns_cursor: null,
  };
  let finish!: (value: unknown) => void;
  let fail!: (reason: Error) => void;
  const GET = vi.fn((_path, options) => {
    const query = options.params.query;
    if (query.expected_continuation_id === "C0")
      return Promise.resolve({ data: original });
    if (!query.turn_id) return Promise.resolve({ data: replacement });
    return new Promise((resolve, reject) => {
      finish = resolve;
      fail = reject;
    });
  });
  const { queryClient, wrapper } = turnHistoryHarness(GET);
  const hook = renderHook(
    ({ continuation }) => useHistory("one", continuation, true),
    { wrapper, initialProps: { continuation: "C0" } },
  );
  await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
  hook.rerender({ continuation: "C1" });
  await waitFor(() => expect(GET).toHaveBeenCalledTimes(3));
  expect(hook.result.current.data?.pages[0]).toEqual(original);
  expect(hook.result.current.isPreviousHistory).toBe(true);
  expect(hook.result.current.hasNextPage).toBe(false);
  await act(async () => fail(new Error("Turn temporarily unavailable")));
  await waitFor(() => expect(hook.result.current.isError).toBe(true));
  expect(hook.result.current.data?.pages[0]).toEqual(original);
  let retry!: Promise<unknown>;
  act(() => {
    retry = hook.result.current.refetch();
  });
  await waitFor(() => expect(GET).toHaveBeenCalledTimes(5));
  await act(async () => {
    finish({
      data: {
        entries: [2, 3, 4, 5].map(turnEntry),
        next_cursor: null,
      },
    });
    await retry;
  });
  await waitFor(() =>
    expect(hook.result.current.data?.pages[0].continuation_id).toBe("C1"),
  );
  expect(hook.result.current.data?.pages[0].entries).toEqual(
    [2, 3, 4, 5].map(turnEntry),
  );
  expect(hook.result.current.isPreviousHistory).toBe(false);
  expect(hook.result.current.hasNextPage).toBe(false);
  hook.unmount();
  queryClient.clear();
});

it("publishes the initial history window without waiting for missing turn details", async () => {
  const page = {
    continuation_id: "C0",
    entries: [turnEntry(5)],
    boundary_entries: [turnEntry(2)],
    turns: [turn],
    next_cursor: "older",
  };
  const GET = vi.fn().mockResolvedValue({ data: page });
  const { queryClient, wrapper } = turnHistoryHarness(GET);
  const hook = renderHook(() => useHistory("one", "C0", true), { wrapper });
  await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
  expect(hook.result.current.data?.pages[0]).toEqual(page);
  expect(GET).toHaveBeenCalledTimes(1);
  hook.unmount();
  queryClient.clear();
});

it("cancels replacement turn pagination without publishing a late checkpoint", async () => {
  let finish!: (value: unknown) => void;
  let oldSignal!: AbortSignal;
  const GET = vi.fn((_path, options) => {
    const continuation = options.params.query.expected_continuation_id;
    if (options.params.query.turn_id) {
      oldSignal = options.signal;
      return new Promise((resolve) => {
        finish = resolve;
      });
    }
    return Promise.resolve({
      data: {
        continuation_id: continuation,
        entries: (continuation === "C1" ? [5] : [2, 3, 4, 5]).map(turnEntry),
        turns: [turn],
        next_cursor: null,
      },
    });
  });
  const { queryClient, wrapper } = turnHistoryHarness(GET);
  const hook = renderHook(
    ({ continuation }) => useHistory("one", continuation, true),
    { wrapper, initialProps: { continuation: "C0" } },
  );
  await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
  hook.rerender({ continuation: "C1" });
  await waitFor(() => expect(GET).toHaveBeenCalledTimes(3));
  hook.rerender({ continuation: "C2" });
  expect(oldSignal.aborted).toBe(true);
  await waitFor(() =>
    expect(hook.result.current.data?.pages[0].continuation_id).toBe("C2"),
  );
  await act(async () =>
    finish({ data: { entries: [turnEntry(5)], next_cursor: "older" } }),
  );
  expect(GET).toHaveBeenCalledTimes(4);
  expect(
    queryClient.getQueryData(["thread", "one", "history", "C1"]),
  ).toBeUndefined();
  expect(hook.result.current.data?.pages[0].continuation_id).toBe("C2");
  hook.unmount();
  queryClient.clear();
});
