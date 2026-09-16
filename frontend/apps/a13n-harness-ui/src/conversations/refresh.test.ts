import { afterEach, expect, it, vi } from "vitest";
import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { refreshThread, scheduleRefresh } from "./refresh";
import { focusRefresh, type FocusFrame } from "./stream";

afterEach(() => vi.useRealTimers());
const event = (event_type: string, payload = {}) =>
  ({ kind: "event", event: { event_type, payload } }) as FocusFrame;
it("does not refresh HTTP queries for presentation events, and routes usage and checkpoints narrowly", () => {
  for (const type of [
    "TEXT_MESSAGE_START",
    "TEXT_MESSAGE_CONTENT",
    "TEXT_MESSAGE_END",
    "TOOL_CALL_START",
    "TOOL_CALL_ARGS",
    "TOOL_CALL_END",
    "TOOL_CALL_RESULT",
    "REASONING_MESSAGE_CONTENT",
  ])
    expect(focusRefresh(event(type))).toBeUndefined();
  expect(
    focusRefresh(event("CUSTOM", { name: "a13n.task.changed", value: {} })),
  ).toBeUndefined();
  expect(
    focusRefresh(
      event("CUSTOM", {
        value: { event: { payload: { type: "usage_report" } } },
      }),
    ),
  ).toBe("usage");
  expect(
    focusRefresh(event("CUSTOM", { name: "a13n.harness_ui.checkpoint" })),
  ).toBe("checkpoint");
  expect(focusRefresh(event("RUN_FINISHED"))).toBe("lifecycle");
  expect(focusRefresh({ kind: "reset" } as FocusFrame)).toBe("reconcile");
});

it("coalesces summary and focused transitions, without refetching saved data or unrelated threads", async () => {
  vi.useFakeTimers();
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: Infinity } },
  });
  const observers: (() => void)[] = [];
  const reads = new Map<string, ReturnType<typeof vi.fn>>();
  for (const section of [
    "detail",
    "configuration",
    "operation",
    "usage",
    "context-usage",
    "history",
    "tasks",
    "children",
  ]) {
    const queryFn = vi.fn(async () => ({}));
    reads.set(section, queryFn);
    const options = { queryKey: ["thread", "one", section], queryFn };
    await client.fetchQuery(options);
    observers.push(new QueryObserver(client, options).subscribe(() => {}));
  }
  client.setQueryData(["thread", "other", "detail"], {});
  for (let i = 0; i < 50; i++) refreshThread(client, "one", "usage");
  await vi.advanceTimersByTimeAsync(200);
  expect(reads.get("usage")).toHaveBeenCalledTimes(2);
  expect(reads.get("context-usage")).toHaveBeenCalledTimes(2);
  expect(reads.get("detail")).toHaveBeenCalledTimes(1);
  expect(reads.get("configuration")).toHaveBeenCalledTimes(1);
  refreshThread(client, "one", "lifecycle");
  refreshThread(client, "one", "lifecycle");
  await vi.advanceTimersByTimeAsync(200);
  expect(reads.get("operation")).toHaveBeenCalledTimes(2);
  expect(reads.get("configuration")).toHaveBeenCalledTimes(2);
  refreshThread(client, "one", "checkpoint");
  await vi.advanceTimersByTimeAsync(200);
  expect(reads.get("configuration")).toHaveBeenCalledTimes(3);
  expect(reads.get("operation")).toHaveBeenCalledTimes(2);
  for (const section of ["history", "tasks", "children"])
    expect(reads.get(section)).toHaveBeenCalledTimes(1);
  expect(
    client.getQueryState(["thread", "other", "detail"])?.isInvalidated,
  ).toBe(false);
  observers.forEach((close) => close());
  client.clear();
});

it("does not revive a cleared authentication cache after a queued reconciliation", async () => {
  vi.useFakeTimers();
  const client = new QueryClient();
  client.setQueryData(["thread", "one", "detail"], {});
  scheduleRefresh(client, () => true);
  client.clear();
  await vi.advanceTimersByTimeAsync(200);
  expect(client.getQueryCache().getAll()).toHaveLength(0);
});
