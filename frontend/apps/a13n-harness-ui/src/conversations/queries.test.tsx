// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { TransportContext } from "../transport/context";
import { ApiError, type Schema, type Transport } from "../transport/client";
import {
  seedThreadSnapshot,
  useHistory,
  useThread,
  useTurnHistory,
} from "./queries";

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

it("does not read a turn already covered by the chat window", async () => {
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

it.each([undefined, "turn-2"])(
  "retains the loaded history range across checkpoints (selected=%s)",
  async (selected) => {
    const page = (continuation: string, index: number) => ({
      continuation_id: continuation,
      entries: [turnEntry(index * 2), turnEntry(index * 2 + 1)],
      turns: [
        {
          ...turn,
          turn_id: `turn-${index}`,
          input_position: index * 2,
          end_position: index * 2 + 2,
        },
      ],
      earlier_turns_cursor: index > 0 ? `${continuation}:${index - 1}` : null,
      later_turns_cursor: index < 4 ? `${continuation}:${index + 1}` : null,
      next_cursor: null,
    });
    let finish!: (value: unknown) => void;
    const GET = vi.fn((_path, options) => {
      const query = options.params.query;
      const index = query.cursor
        ? Number(query.cursor.split(":")[1])
        : selected
          ? 2
          : 4;
      if (query.cursor === "C1:1")
        return new Promise((resolve) => {
          finish = resolve;
        });
      return Promise.resolve({ data: page("C1", index) });
    });
    const { queryClient, wrapper } = turnHistoryHarness(GET);
    queryClient.setQueryData(
      ["thread", "one", "history", "C0", ...(selected ? [selected] : [])],
      {
        pages: [page("C0", 3), page("C0", 2), page("C0", 1)],
        pageParams: [undefined, "C0:2", "C0:1"],
      },
    );
    const hook = renderHook(() => useHistory("one", "C1", true, selected), {
      wrapper,
    });
    await waitFor(() =>
      expect(
        GET.mock.calls.some(
          ([, options]) => options.params.query.cursor === "C1:1",
        ),
      ).toBe(true),
    );
    expect(hook.result.current.data?.pages[0].continuation_id).toBe("C0");
    await act(async () => finish({ data: page("C1", 1) }));
    await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
    const pages = hook.result.current.data!.pages;
    expect(
      pages
        .flatMap((page) => page.turns ?? [])
        .map((turn) => turn.turn_id)
        .sort(),
    ).toEqual(
      selected
        ? ["turn-1", "turn-2", "turn-3"]
        : ["turn-1", "turn-2", "turn-3", "turn-4"],
    );
    expect(
      pages
        .flatMap((page) => page.entries)
        .map((entry) => entry.position)
        .sort((a, b) => a - b),
    ).toEqual(selected ? [2, 3, 4, 5, 6, 7] : [2, 3, 4, 5, 6, 7, 8, 9]);
    expect(pages.at(-1)?.earlier_turns_cursor).toBe("C1:0");
    expect(pages[0].later_turns_cursor).toBe(selected ? "C1:4" : null);
    expect(
      GET.mock.calls.every(
        ([, options]) =>
          options.params.query.expected_continuation_id === "C1" &&
          !options.params.query.cursor?.startsWith("C0:"),
      ),
    ).toBe(true);
    hook.unmount();
    queryClient.clear();
  },
);

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
      if (continuation === "C1") oldSignal = options.signal;
      if (continuation === "C2")
        return Promise.resolve({
          data: { entries: [2, 3, 4, 5].map(turnEntry), next_cursor: null },
        });
      return new Promise((resolve) => {
        finish = resolve;
      });
    }
    return Promise.resolve({
      data: {
        continuation_id: continuation,
        entries: (continuation === "C0" ? [2, 3, 4, 5] : [5]).map(turnEntry),
        turns: [{ ...turn, content_revision: "same" }],
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
  expect(GET).toHaveBeenCalledTimes(5);
  expect(
    queryClient.getQueryData(["thread", "one", "history", "C1"]),
  ).toBeUndefined();
  expect(hook.result.current.data?.pages[0].continuation_id).toBe("C2");
  hook.unmount();
  queryClient.clear();
});

it("reuses complete turn entries only with the same server content revision", async () => {
  const GET = vi.fn().mockImplementation((_path, options) => ({
    data: {
      continuation_id: options.params.query.expected_continuation_id,
      entries: [
        { position: 0, message_kind: "request", parts: [] },
        {
          position: 1,
          message_kind: "response",
          parts: [{ kind: "text", text: "answer" }],
        },
      ],
      next_cursor: null,
    },
  }));
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const turn = {
    turn_id: "input",
    input_position: 0,
    end_position: 2,
    preview: "input",
    content_revision: "same",
  } as Schema<"TranscriptTurn">;
  const hook = renderHook(
    ({ continuation, revision }) =>
      useTurnHistory(
        "one",
        continuation,
        { ...turn, content_revision: revision },
        true,
      ),
    {
      initialProps: { continuation: "C1", revision: "same" },
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>
          <TransportContext value={{ client: { GET } } as unknown as Transport}>
            {children}
          </TransportContext>
        </QueryClientProvider>
      ),
    },
  );
  await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
  expect(GET).toHaveBeenCalledTimes(1);
  hook.rerender({ continuation: "C2", revision: "same" });
  expect(hook.result.current.isSuccess).toBe(true);
  expect(GET).toHaveBeenCalledTimes(1);
  hook.rerender({ continuation: "C3", revision: "corrected" });
  await waitFor(() => expect(GET).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
  hook.unmount();
  client.clear();
});

it("refreshes detail and opens the selected turn on the current checkpoint after a history conflict", async () => {
  let head = "C1";
  const GET = vi.fn(async (path: string, options) => {
    if (path.endsWith("/transcript")) {
      const q = options.params.query;
      expect(q.turn_id).toBe("input-one");
      if (q.expected_continuation_id === "C1") {
        head = "C2";
        throw new ApiError(
          "History changed",
          400,
          "thread_history_continuation_changed",
        );
      }
      return {
        data: {
          continuation_id: head,
          entries: [],
          turns: [],
          next_cursor: null,
        },
      };
    }
    return {
      data: {
        thread: { thread_id: "one", configuration: {} },
        continuation_id: head,
      },
    };
  });
  const { wrapper } = turnHistoryHarness(GET);
  const hook = renderHook(
    () => {
      const detail = useThread("one");
      return useHistory(
        "one",
        detail.data?.continuation_id,
        !!detail.data,
        "input-one",
      );
    },
    { wrapper },
  );
  await waitFor(() =>
    expect(hook.result.current.data?.pages[0].continuation_id).toBe("C2"),
  );
  expect(
    GET.mock.calls.filter(([path]) => path.endsWith("/transcript")),
  ).toHaveLength(2);
});
