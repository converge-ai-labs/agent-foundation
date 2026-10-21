import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import { usePanelData } from "./queries";
import type { Panel } from "./api";
const state = vi.hoisted(() => ({ POST: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ workspace: () => state }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" }, can: () => true }),
}));
const response = (value: string) => ({
  data: {
    status: "success",
    data: {
      resultType: "matrix",
      result: [{ metric: {}, values: [[60, value]] }],
    },
  },
  response: new Response(),
});
const panel: Panel = {
  id: "panel",
  title: "Test",
  position: { x: 0, y: 0, w: 6, h: 5 },
  queries: [{ kind: "promql", source_id: "mds_test", expression: "up" }],
};
function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={cache}>{children}</QueryClientProvider>;
}
let cache: QueryClient;
it("retains one previous response during refresh/error but never across query identities", async () => {
  cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let reject!: (error: Error) => void;
  state.POST.mockReset()
    .mockResolvedValueOnce(response("1"))
    .mockImplementation(
      () =>
        new Promise((_resolve, failure) => {
          reject = failure;
        }),
    );
  const { result, rerender, unmount } = renderHook(
    ({ revision, source }) =>
      usePanelData(
        {
          ...panel,
          queries: [{ kind: "promql", source_id: source, expression: "up" }],
        },
        null,
        { start: 0, end: 3600 + revision },
        revision,
      ),
    { wrapper, initialProps: { revision: 0, source: "mds_test" } },
  );
  await waitFor(() => expect(result.current.data.series).toHaveLength(1));
  rerender({ revision: 1, source: "mds_test" });
  await waitFor(() => expect(state.POST).toHaveBeenCalledTimes(2));
  expect(result.current.stale).toBe(true);
  expect(result.current.data.series[0].points).toEqual([[60, 1]]);
  await act(async () => reject(new Error("receiver unavailable")));
  await waitFor(() => expect(result.current.errors).toHaveLength(1));
  expect(result.current.stale).toBe(true);
  rerender({ revision: 2, source: "mds_other" });
  expect(result.current.data.series).toHaveLength(0);
  expect(result.current.stale).toBe(false);
  unmount();
  cache.clear();
});
it("admits at most four requests and never starts queued work after unmount", async () => {
  cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const signals: AbortSignal[] = [];
  state.POST.mockReset().mockImplementation(
    (_path, args: { signal: AbortSignal }) => {
      signals.push(args.signal);
      return new Promise((_resolve, reject) =>
        args.signal.addEventListener("abort", () => reject(args.signal.reason)),
      );
    },
  );
  const { unmount } = renderHook(
    () => {
      usePanelData(
        {
          ...panel,
          queries: Array.from({ length: 4 }, (_, i) => ({
            kind: "promql",
            source_id: "mds_test",
            expression: `up{slot="${i}"}`,
          })),
        },
        null,
        { start: 0, end: 3600 },
        0,
      );
      usePanelData(
        {
          ...panel,
          queries: Array.from({ length: 4 }, (_, i) => ({
            kind: "promql",
            source_id: "mds_test",
            expression: `up{slot="${i + 4}"}`,
          })),
        },
        null,
        { start: 0, end: 3600 },
        0,
      );
    },
    { wrapper },
  );
  await waitFor(() => expect(state.POST).toHaveBeenCalledTimes(4));
  unmount();
  await waitFor(() =>
    expect(signals.every((signal) => signal.aborted)).toBe(true),
  );
  await act(async () => {
    await Promise.resolve();
  });
  expect(state.POST).toHaveBeenCalledTimes(4);
  cache.clear();
});
