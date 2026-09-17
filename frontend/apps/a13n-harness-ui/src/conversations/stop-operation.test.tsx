// @vitest-environment jsdom
import { useSyncExternalStore, type ReactNode } from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, type Schema, type Transport } from "../transport/client";
import { TransportContext } from "../transport/context";
import { ThreadDraft } from "./draft";
import { useStopOperation } from "./stop-operation";

const clients: QueryClient[] = [];
afterEach(() => {
  cleanup();
  for (const client of clients.splice(0)) client.clear();
});
function status(value: Schema<"RootOperationStatus">, receipt = "one") {
  return {
    data: {
      receipt: { receipt_id: receipt, thread_id: "thread" },
      status: value,
    },
  };
}
function fixture() {
  const draft = new ThreadDraft();
  const POST = vi.fn().mockResolvedValue({
    data: { receipt_id: "one", accepted: true },
  });
  const GET = vi.fn().mockResolvedValue(status("running"));
  const transport = { client: { POST, GET } } as unknown as Transport;
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(query);
  const reconcile = vi.fn();
  const mount = () =>
    renderHook(
      ({ receipt }: { receipt?: string }) => {
        useSyncExternalStore(draft.subscribe, draft.getSnapshot);
        return useStopOperation(
          "thread",
          draft,
          receipt
            ? {
                state: "running",
                receipt_id: receipt,
                available_actions: ["cancel", "steer"],
              }
            : { state: "inactive" },
          reconcile,
        );
      },
      {
        initialProps: { receipt: "one" } as { receipt?: string },
        wrapper: ({ children }: { children: ReactNode }) => (
          <QueryClientProvider client={query}>
            <TransportContext value={transport}>{children}</TransportContext>
          </QueryClientProvider>
        ),
      },
    );
  return { draft, POST, GET, query, reconcile, mount };
}

it("keeps accepted Stop pending through running status and confirms the exact terminal receipt without SSE", async () => {
  const f = fixture();
  const hook = f.mount();
  await act(() => hook.result.current.stop());
  await waitFor(() => expect(f.GET).toHaveBeenCalledTimes(1));
  expect(hook.result.current.stopping).toBe(true);
  expect(hook.result.current.canStop).toBe(false);
  expect(hook.result.current.request?.message).toContain("cleanup");
  expect(hook.result.current.request?.outcome).toBeUndefined();
  f.GET.mockResolvedValue(status("cancelled"));
  await waitFor(
    () => expect(hook.result.current.request?.outcome).toBe("cancelled"),
    { timeout: 2500 },
  );
  expect(f.GET.mock.calls[0][1].params.path.receipt_id).toBe("one");
  expect(f.POST).toHaveBeenCalledTimes(1);
  expect(f.reconcile).toHaveBeenCalledTimes(2);
  hook.rerender({ receipt: undefined });
  expect(hook.result.current.request).toBeUndefined();
  expect(hook.result.current.stopping).toBe(false);
});

it("latches concurrent clicks before React commits and retains the request across navigation", async () => {
  const f = fixture();
  let resolve!: (value: unknown) => void;
  f.POST.mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  let hook = f.mount();
  let first!: Promise<void>;
  act(() => {
    first = hook.result.current.stop();
    void hook.result.current.stop();
  });
  expect(f.POST).toHaveBeenCalledTimes(1);
  hook.unmount();
  await act(async () => {
    resolve({ data: { receipt_id: "one", accepted: true } });
    await first;
  });
  hook = f.mount();
  expect(hook.result.current.stopping).toBe(true);
  await waitFor(() => expect(f.GET).toHaveBeenCalled());
  expect(f.POST).toHaveBeenCalledTimes(1);
});

it("does not let a late old acknowledgement or failure control a replacement receipt", async () => {
  const f = fixture();
  let reject!: (error: Error) => void;
  f.POST.mockReturnValueOnce(
    new Promise((_done, fail) => {
      reject = fail;
    }),
  );
  const hook = f.mount();
  let first!: Promise<void>;
  act(() => {
    first = hook.result.current.stop();
  });
  hook.rerender({ receipt: "two" });
  expect(hook.result.current.canStop).toBe(true);
  expect(hook.result.current.request).toBeUndefined();
  f.POST.mockResolvedValue({ data: { receipt_id: "two", accepted: true } });
  f.GET.mockResolvedValue(status("running", "two"));
  await act(() => hook.result.current.stop());
  await act(async () => {
    reject(new Error("Old response lost"));
    await first;
  });
  expect(hook.result.current.request?.receipt).toBe("two");
  expect(hook.result.current.request?.phase).toBe("waiting");
  expect(
    f.POST.mock.calls.map((call) => call[1].params.path.receipt_id),
  ).toEqual(["one", "two"]);
});

it.each(["completed", "failed", "suspended"] as const)(
  "reconciles accepted=false to %s without claiming cancellation",
  async (outcome) => {
    const f = fixture();
    f.POST.mockResolvedValue({ data: { receipt_id: "one", accepted: false } });
    // Keep the non-accepted acknowledgement visible before final observation.
    let resolve!: (value: unknown) => void;
    f.GET.mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const hook = f.mount();
    await act(() => hook.result.current.stop());
    expect(hook.result.current.request?.message).toContain("already ended");
    await act(async () => {
      resolve(status(outcome));
    });
    await waitFor(() =>
      expect(hook.result.current.request?.outcome).toBe(outcome),
    );
    expect(hook.result.current.request?.message).not.toContain("stopped");
    expect(f.POST).toHaveBeenCalledTimes(1);
  },
);

it.each([
  [new TypeError("Lost response"), "uncertain"],
  [new DOMException("Timed out", "TimeoutError"), "uncertain"],
  [new ApiError("Proxy failure", 503), "uncertain"],
  [new ApiError("Access denied", 403), "rejected"],
] as const)(
  "inspects a failed acknowledgement (%s) before allowing a deliberate retry",
  async (error, phase) => {
    const f = fixture();
    f.POST.mockRejectedValueOnce(error);
    let resolve!: (value: unknown) => void;
    f.GET.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done;
      }),
    );
    const hook = f.mount();
    await act(() => hook.result.current.stop());
    expect(hook.result.current.request?.phase).toBe(phase);
    expect(hook.result.current.canStop).toBe(false);
    await act(async () => {
      resolve(status("running"));
    });
    await waitFor(() => expect(hook.result.current.retryable).toBe(true));
    expect(f.POST).toHaveBeenCalledTimes(1);
    await act(() => hook.result.current.stop());
    expect(f.POST).toHaveBeenCalledTimes(2);
    expect(hook.result.current.request?.phase).toBe("waiting");
  },
);

it("recovers from a lost acknowledgement followed by cancelled status without another POST", async () => {
  const f = fixture();
  f.POST.mockRejectedValue(new Error("Connection lost"));
  f.GET.mockResolvedValue(status("cancelled"));
  const hook = f.mount();
  await act(() => hook.result.current.stop());
  await waitFor(() =>
    expect(hook.result.current.request?.outcome).toBe("cancelled"),
  );
  expect(hook.result.current.canStop).toBe(false);
  expect(f.POST).toHaveBeenCalledTimes(1);
});

it("keeps unavailable observations explicit and refreshes without replaying Stop", async () => {
  const f = fixture();
  f.GET.mockRejectedValue(new ApiError("Receipt unavailable", 404));
  const hook = f.mount();
  await act(() => hook.result.current.stop());
  await waitFor(() => expect(hook.result.current.observationFailed).toBe(true));
  expect(hook.result.current.request?.outcome).toBeUndefined();
  expect(hook.result.current.canStop).toBe(false);
  f.GET.mockResolvedValue(status("cancelled"));
  act(() => hook.result.current.refresh());
  await waitFor(() =>
    expect(hook.result.current.request?.outcome).toBe("cancelled"),
  );
  expect(f.POST).toHaveBeenCalledTimes(1);
});

it("rejects mismatched receipt acknowledgements and observations", async () => {
  const f = fixture();
  f.POST.mockResolvedValue({ data: { receipt_id: "another", accepted: true } });
  f.GET.mockResolvedValue(status("cancelled", "another"));
  const hook = f.mount();
  await act(() => hook.result.current.stop());
  await waitFor(() => expect(hook.result.current.observationFailed).toBe(true));
  expect(hook.result.current.request?.phase).toBe("uncertain");
  expect(hook.result.current.request?.outcome).toBeUndefined();
  expect(hook.result.current.canStop).toBe(false);
});

it("rejects a stale click callback even after an immediate acknowledgement", async () => {
  const f = fixture();
  const hook = f.mount();
  const click = hook.result.current.stop;
  await act(async () => {
    await click();
    await click();
  });
  expect(f.POST).toHaveBeenCalledTimes(1);
});

it("requires a fresh observation after every uncertain retry, not cached running status", async () => {
  const f = fixture();
  f.POST.mockRejectedValue(new Error("Lost response"));
  const hook = f.mount();
  await act(() => hook.result.current.stop());
  await waitFor(() => expect(hook.result.current.retryable).toBe(true));
  let resolve!: (value: unknown) => void;
  f.GET.mockReturnValueOnce(
    new Promise((done) => {
      resolve = done;
    }),
  );
  await act(() => hook.result.current.stop());
  await waitFor(() => expect(f.GET).toHaveBeenCalledTimes(2));
  expect(hook.result.current.retryable).toBe(false);
  expect(hook.result.current.canStop).toBe(false);
  await act(() => hook.result.current.stop());
  expect(f.POST).toHaveBeenCalledTimes(2);
  await act(async () => {
    resolve(status("running"));
  });
  await waitFor(() => expect(hook.result.current.retryable).toBe(true));
});

it("does not let a manual refresh observe before the cancellation acknowledgement", async () => {
  const f = fixture();
  let resolve!: (value: unknown) => void;
  f.POST.mockReturnValueOnce(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const hook = f.mount();
  let sending!: Promise<void>;
  act(() => {
    sending = hook.result.current.stop();
  });
  act(() => hook.result.current.refresh());
  expect(f.GET).not.toHaveBeenCalled();
  await act(async () => {
    resolve({ data: { receipt_id: "one", accepted: true } });
    await sending;
  });
  await waitFor(() => expect(f.GET).toHaveBeenCalledTimes(1));
});
