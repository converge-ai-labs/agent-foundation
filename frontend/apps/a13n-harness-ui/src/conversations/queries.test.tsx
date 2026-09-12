// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { TransportContext } from "../transport/context";
import type { Transport } from "../transport/client";
import { useHistory } from "./queries";

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
