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

it("patches content-only updates in the starred supplement without refreshing", () => {
  const client = new QueryClient();
  const starred = row("starred");
  starred.thread.starred = true;
  const data = page([row("ordinary")]);
  data.pages[0].starred_rows = [starred];
  client.setQueryData(key("one"), data);
  const changed = {
    ...starred,
    latest_activity: { kind: "assistant", text: "Updated star" },
  } as Row;
  applyActivityUpdates(client, [changed], ["starred"]);
  expect(
    client.getQueryData<InfiniteData<Page>>(key("one"))?.pages[0].starred_rows,
  ).toEqual([changed]);
  expect(client.getQueryState(key("one"))?.isInvalidated).toBe(false);
  client.clear();
});

it.each(["archive", "move", "delete"])(
  "removes a starred supplement row after a remote %s and reconciles membership",
  async (operation) => {
    vi.useFakeTimers();
    const client = new QueryClient();
    const starred = row("starred");
    starred.thread.starred = true;
    const data = page([row("ordinary")]);
    data.pages[0].starred_rows = [starred];
    client.setQueryData(key("one"), data);
    client.setQueryData(key("two"), page([]));
    const next = row("starred", operation === "move" ? "two" : "one");
    next.thread.starred = true;
    next.thread.archived = operation === "archive";
    applyActivityUpdates(client, operation === "delete" ? [] : [next], [
      "starred",
    ]);
    expect(
      client.getQueryData<InfiniteData<Page>>(key("one"))?.pages[0]
        .starred_rows,
    ).toEqual([]);
    await vi.advanceTimersByTimeAsync(200);
    expect(client.getQueryState(key("one"))?.isInvalidated).toBe(true);
    expect(client.getQueryState(key("two"))?.isInvalidated).toBe(
      operation === "move",
    );
    client.clear();
  },
);

it.each([false, true])(
  "reconciles the ordinary quota when starred changes from %s",
  async (wasStarred) => {
    vi.useFakeTimers();
    const client = new QueryClient();
    const old = row("changed");
    old.thread.starred = wasStarred;
    const data = page(wasStarred ? [] : [old]);
    data.pages[0].starred_rows = wasStarred ? [old] : [];
    client.setQueryData(key("one"), data);
    client.setQueryData(key("two"), page([]));
    const next = row("changed");
    next.thread.starred = !wasStarred;
    applyActivityUpdates(client, [next], ["changed"]);
    const updated = client.getQueryData<InfiniteData<Page>>(key("one"))
      ?.pages[0];
    expect(wasStarred ? updated?.starred_rows : updated?.rows).toEqual([next]);
    await vi.advanceTimersByTimeAsync(200);
    expect(client.getQueryState(key("one"))?.isInvalidated).toBe(true);
    expect(client.getQueryState(key("two"))?.isInvalidated).toBe(false);
    client.clear();
  },
);
