// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";
import { seedThreadSnapshot, useHistory } from "./queries";

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
