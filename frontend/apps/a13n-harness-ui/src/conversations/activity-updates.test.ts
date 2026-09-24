// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { QueryClient, type InfiniteData } from "@tanstack/react-query";
import { createTransport, type Schema } from "../transport/client";
import { applyActivityUpdates, refreshActivity } from "./activity-updates";

type Row = Schema<"ThreadActivityView">;
type Page = Schema<"ThreadActivityPage"> & { observedAt: number };
const key = (project: string, query = "") => [
  "threads",
  query,
  project,
  false,
  "all",
  5,
  false,
  true,
];
const row = (id: string, project = "one"): Row =>
  ({
    thread: {
      thread_id: id,
      title: id,
      configuration: { project_id: project },
      root_activity: { state: "inactive" },
      archived: false,
      touched_at: "2026-09-17T00:00:00Z",
    },
  }) as Row;
const page = (rows: Row[]): InfiniteData<Page> => ({
  pages: [{ rows, active_rows: [], total: rows.length, observedAt: 0 }],
  pageParams: [undefined],
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("patches only dirty loaded rows without invalidating content-only pages or other projects", () => {
  const client = new QueryClient();
  client.setQueryData(key("one"), page([row("changed"), row("stable")]));
  client.setQueryData(key("two"), page([row("other", "two")]));
  const before = client.getQueryData(key("two"));
  const changed = {
    ...row("changed"),
    latest_activity: { kind: "assistant", text: "Updated" },
  } as Row;
  applyActivityUpdates(client, [changed], ["changed"]);
  expect(
    client.getQueryData<InfiniteData<Page>>(key("one"))?.pages[0].rows[0],
  ).toEqual(changed);
  expect(client.getQueryState(key("one"))?.isInvalidated).toBe(false);
  expect(client.getQueryData(key("two"))).toBe(before);
  client.clear();
});

it("reconciles old and new project membership, search and active partition without refreshing unrelated lists", async () => {
  vi.useFakeTimers();
  const client = new QueryClient();
  client.setQueryData(key("one"), page([row("moving")]));
  client.setQueryData(key("two"), page([]));
  client.setQueryData(key("three"), page([]));
  client.setQueryData(key("two", "needle"), page([]));
  applyActivityUpdates(client, [row("moving", "two")], ["moving"]);
  await vi.advanceTimersByTimeAsync(200);
  expect(client.getQueryState(key("one"))?.isInvalidated).toBe(true);
  expect(client.getQueryState(key("two"))?.isInvalidated).toBe(true);
  expect(client.getQueryState(key("two", "needle"))?.isInvalidated).toBe(true);
  expect(client.getQueryState(key("three"))?.isInvalidated).toBe(false);
  client.clear();
});

it("coalesces dirty IDs into one lookup and preserves hints arriving during a lookup", async () => {
  vi.useFakeTimers();
  const client = new QueryClient();
  client.setQueryData(key("one"), page([row("a"), row("b")]));
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const reads: string[][] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const { thread_ids } = await request.json();
      reads.push(thread_ids);
      if (reads.length === 1) await gate;
      return Response.json(thread_ids.map((id: string) => row(id)));
    }),
  );
  const transport = createTransport("", vi.fn());
  refreshActivity(client, transport, "a");
  refreshActivity(client, transport, "a");
  await vi.advanceTimersByTimeAsync(100);
  refreshActivity(client, transport, "b");
  release();
  await vi.advanceTimersByTimeAsync(0);
  expect(reads).toEqual([["a"], ["b"]]);
  client.clear();
  transport.close();
});

it("reconciles empty destination lists after a failed lookup without waiting for another hint", async () => {
  vi.useFakeTimers();
  const client = new QueryClient();
  client.setQueryData(key(""), page([]));
  client.setQueryData(key("destination"), page([]));
  const fetcher = vi.fn(async () => {
    throw new TypeError("Transient lookup failure");
  });
  vi.stubGlobal("fetch", fetcher);
  const transport = createTransport("", vi.fn());
  refreshActivity(client, transport, "new-thread");
  await vi.advanceTimersByTimeAsync(400);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(client.getQueryState(key(""))?.isInvalidated).toBe(true);
  expect(client.getQueryState(key("destination"))?.isInvalidated).toBe(true);
  client.clear();
  transport.close();
});

it("does not let unrelated slow pagination block dirty Thread lookups", async () => {
  vi.useFakeTimers();
  const client = new QueryClient();
  client.setQueryData(key("one"), page([row("changed")]));
  let release!: (value: InfiniteData<Page>) => void;
  const pending = client.fetchQuery({
    queryKey: key("two"),
    queryFn: () =>
      new Promise<InfiniteData<Page>>((resolve) => {
        release = resolve;
      }),
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json([
        {
          ...row("changed"),
          latest_activity: { kind: "assistant", text: "Fresh" },
        },
      ]),
    ),
  );
  const transport = createTransport("", vi.fn());
  try {
    refreshActivity(client, transport, "changed");
    await vi.advanceTimersByTimeAsync(100);
    expect(fetch).toHaveBeenCalledOnce();
    expect(
      client.getQueryData<InfiniteData<Page>>(key("one"))?.pages[0].rows[0]
        .latest_activity?.text,
    ).toBe("Fresh");
    expect(client.getQueryState(key("two"))?.fetchStatus).toBe("fetching");
  } finally {
    release(page([row("other", "two")]));
    await pending;
    client.clear();
    transport.close();
  }
});

it("reconciles an initial list read that finishes after its activity hint", async () => {
  vi.useFakeTimers();
  const client = new QueryClient();
  let release!: (value: InfiniteData<Page>) => void;
  const pending = client.fetchQuery({
    queryKey: key("one"),
    queryFn: () =>
      new Promise<InfiniteData<Page>>((resolve) => {
        release = resolve;
      }),
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json([row("new")])),
  );
  const transport = createTransport("", vi.fn());
  try {
    refreshActivity(client, transport, "new");
    await vi.advanceTimersByTimeAsync(300);
    expect(fetch).toHaveBeenCalledOnce();
    expect(client.getQueryData(key("one"))).toBeUndefined();
    release(page([]));
    await pending;
    await vi.advanceTimersByTimeAsync(0);
    expect(client.getQueryState(key("one"))?.isInvalidated).toBe(true);
  } finally {
    release(page([]));
    await pending;
    client.clear();
    transport.close();
  }
});
