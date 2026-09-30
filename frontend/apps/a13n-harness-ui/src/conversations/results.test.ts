// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { IDBFactory } from "fake-indexeddb";
import { createTransport, type Schema } from "../transport/client";
import { ResultStore } from "./result-store";
import { ResultTracker } from "./results";
import { savedResultVisible } from "./result-visibility";

let threads: Map<string, Schema<"ThreadSummary">>;
let lookups: string[][];
let trackers: ResultTracker[];
let failLookup: boolean;
function thread(id: string, version = 0): Schema<"ThreadSummary"> {
  return {
    thread_id: id,
    parent_thread_id: null,
    title: id,
    archived: false,
    created_at: "2026-09-16T00:00:00Z",
    updated_at: "2026-09-16T00:00:00Z",
    metadata_version: 0,
    configuration: { project_id: null },
    root_activity: { state: "inactive" },
    completion: version
      ? {
          version,
          run_id: `run-${version}`,
          continuation_id: "a".repeat(64),
          completed_at: "2026-09-16T00:00:00Z",
        }
      : null,
  } as Schema<"ThreadSummary">;
}
function tracker(store = new ResultStore()) {
  const item = new ResultTracker(
    createTransport("test", () => {}),
    store,
  );
  trackers.push(item);
  return item;
}
beforeEach(() => {
  threads = new Map();
  lookups = [];
  trackers = [];
  failLookup = false;
  vi.stubGlobal("indexedDB", new IDBFactory());
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path === "/api/threads/lookup") {
        const { thread_ids } = await request.json();
        lookups.push(thread_ids);
        if (failLookup) throw new TypeError("Offline");
        return Response.json({
          threads: thread_ids.flatMap((id: string) =>
            threads.has(id) ? [threads.get(id)] : [],
          ),
          total: thread_ids.length,
        });
      }
      return Response.json({
        thread: threads.get(decodeURIComponent(path.split("/").at(-1)!)),
      });
    }),
  );
});
afterEach(async () => {
  for (const item of trackers) await item.refresh();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("atomically merges tab interests, ignores late baselines, and never regresses acknowledgements", async () => {
  const first = new ResultStore();
  const second = new ResultStore();
  await Promise.all([
    first.update("one", 0, true),
    second.update("two", 3, true),
  ]);
  await Promise.all([
    first.update("one", 2, false),
    second.update("one", 1, false),
    second.update("one", 9, true),
  ]);
  expect(await first.read()).toEqual([
    { threadId: "one", acknowledged: 2 },
    { threadId: "two", acknowledged: 3 },
  ]);
  expect(await first.update("not-followed", 8, false)).toBeUndefined();
});

it("publishes nothing when a successful put is followed by an aborted transaction", async () => {
  const { IDBObjectStore } = await import("fake-indexeddb");
  const original = IDBObjectStore.prototype.put;
  vi.spyOn(IDBObjectStore.prototype, "put").mockImplementation(function (
    this: IDBObjectStore,
    value,
    key,
  ) {
    const request = original.call(this, value, key);
    request.addEventListener("success", () => this.transaction.abort());
    return request;
  });
  const store = new ResultStore();
  await expect(store.update("one", 4, true)).rejects.toThrow();
  expect(await store.read()).toEqual([]);
});

it("baselines first visits, follows before fast Send, and restores results independently of pagination", async () => {
  const first = tracker();
  threads.set("historical", thread("historical", 4));
  await first.follow(threads.get("historical")!);
  expect(first.isUnread("historical")).toBe(false);
  threads.set("fast", thread("fast"));
  await first.beforeRun("fast");
  expect(await new ResultStore().read()).toContainEqual({
    threadId: "fast",
    acknowledged: 0,
  });
  threads.set("fast", thread("fast", 1));
  const reopened = tracker();
  await reopened.refresh();
  expect(reopened.isUnread("fast")).toBe(true);
  expect(reopened.isUnread("historical")).toBe(false);
  expect(lookups.at(-1)).toEqual(["fast", "historical"]);
});

it("retains unseen success through later running work, stale observations, and lookup failures", async () => {
  const item = tracker();
  const initial = thread("one");
  await item.follow(initial);
  threads.set("one", thread("one", 2));
  await item.refresh();
  item.observe(
    {
      ...thread("one", 2),
      root_activity: { state: "running", run_id: "run-next" },
    },
    Date.now() + 10,
  );
  item.observe(thread("one", 1), Date.now() + 20);
  expect(item.isUnread("one")).toBe(true);
  expect(
    item.getSnapshot().threads.get("one")?.thread.completion?.version,
  ).toBe(2);
  failLookup = true;
  await item.refresh();
  expect(item.isUnread("one")).toBe(true);
  expect(item.getSnapshot().lookupError).toContain("retained");
  // The user rendered version 1, even though a lookup already knows version 2.
  await item.acknowledge("one", 1);
  expect(item.isUnread("one")).toBe(true);
  await item.acknowledge("one", 2);
  expect(item.isUnread("one")).toBe(false);
});

it("marks only supplied result versions read and preserves later completions", async () => {
  const item = tracker();
  for (const id of ["one", "two", "other-project"])
    await item.follow(thread(id));
  for (const id of ["one", "two", "other-project", "not-followed"])
    item.observe(thread(id, 1));
  const selected = [
    thread("one", 1),
    thread("two", 1),
    thread("not-followed", 1),
  ];
  const pending = item.acknowledgeAll(selected);
  item.observe(thread("one", 2));
  await pending;
  expect(item.isUnread("one")).toBe(true);
  expect(item.isUnread("two")).toBe(false);
  expect(item.isUnread("other-project")).toBe(true);
  expect(await new ResultStore().read()).not.toContainEqual({
    threadId: "not-followed",
    acknowledged: 1,
  });
});

it("retains failed bulk acknowledgements for the existing storage retry", async () => {
  const store = new ResultStore();
  const item = tracker(store);
  await item.follow(thread("one"));
  item.observe(thread("one", 1));
  const update = vi
    .spyOn(store, "update")
    .mockRejectedValue(new Error("Quota"));
  await item.acknowledgeAll([thread("one", 1)]);
  expect(item.isUnread("one")).toBe(true);
  expect(item.getSnapshot().storageError).toContain("could not be saved");
  update.mockRestore();
  threads.set("one", thread("one", 2));
  await item.refresh();
  expect(item.getSnapshot().followed.get("one")).toBe(1);
  expect(item.isUnread("one")).toBe(true);
});

it("rereads committed tab state and keeps more than 256 followed roots in bounded batches", async () => {
  const store = new ResultStore();
  await Promise.all(
    Array.from({ length: 260 }, (_, index) =>
      store.update(`thread-${index}`, 0, true),
    ),
  );
  threads.set("thread-0", thread("thread-0", 2));
  const otherTab = tracker();
  await otherTab.refresh();
  expect(lookups.map((ids) => ids.length)).toEqual([100, 100, 60]);
  expect(otherTab.isUnread("thread-0")).toBe(true);
  await store.update("thread-0", 2, false);
  await otherTab.refresh();
  expect(otherTab.isUnread("thread-0")).toBe(false);
  expect(otherTab.getSnapshot().followed.size).toBe(260);
});

it("retains the original follow baseline and failed read intent while storage is unavailable", async () => {
  const store = new ResultStore();
  const update = vi
    .spyOn(store, "update")
    .mockRejectedValue(new Error("Quota"));
  const item = tracker(store);
  await item.follow(thread("one", 1));
  await item.follow(thread("one", 2));
  expect(item.getSnapshot().followed.size).toBe(0);
  expect(item.getSnapshot().storageError).toContain("could not be saved");
  await item.refresh();
  expect(item.getSnapshot().storageError).toContain("could not be saved");
  update.mockRestore();
  threads.set("one", thread("one", 2));
  await item.refresh();
  expect(item.getSnapshot().followed.get("one")).toBe(1);
  expect(item.isUnread("one")).toBe(true);
  vi.spyOn(store, "update").mockRejectedValue(new Error("Quota"));
  await item.acknowledge("one", 2);
  expect(item.isUnread("one")).toBe(true);
  vi.restoreAllMocks();
  await item.refresh();
  expect(item.isUnread("one")).toBe(false);
  expect(item.getSnapshot().storageError).toBe("");
});

it("coalesces repeated refresh hints and queries again after an in-flight observation", async () => {
  const item = tracker();
  threads.set("one", thread("one"));
  await item.follow(thread("one"));
  await item.refresh();
  lookups = [];
  for (let index = 0; index < 30; index++) item.invalidate("one");
  await item.refresh();
  expect(lookups).toHaveLength(1);
  const fetch = globalThis.fetch;
  let release!: () => void;
  const paused = new Promise<void>((resolve) => {
    release = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      await paused;
      return fetch(request);
    }),
  );
  const refreshing = item.refresh();
  await vi.waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
  threads.set("one", thread("one", 1));
  item.invalidate("one");
  release();
  await refreshing;
  expect(lookups).toHaveLength(3);
  expect(item.isUnread("one")).toBe(true);
});

it("requires foreground, rendered dimensions, and the actual transcript bottom", () => {
  const element = document.createElement("div");
  document.body.append(element);
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  Object.defineProperties(element, {
    clientHeight: { value: 200 },
    scrollHeight: { value: 1000 },
  });
  vi.spyOn(element, "getClientRects").mockReturnValue([
    {},
  ] as unknown as DOMRectList);
  vi.spyOn(element, "getBoundingClientRect").mockReturnValue({
    top: 0,
    bottom: 200,
    left: 0,
    right: 400,
  } as DOMRect);
  element.scrollTop = 790;
  expect(savedResultVisible(element)).toBe(false);
  element.scrollTop = 800;
  expect(savedResultVisible(element)).toBe(true);
  vi.spyOn(document, "hasFocus").mockReturnValue(false);
  expect(savedResultVisible(element)).toBe(false);
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  element.hidden = true;
  expect(savedResultVisible(element)).toBe(false);
  element.remove();
});

it("looks up only dirty followed IDs instead of the complete interest set", async () => {
  const item = tracker();
  for (const id of ["one", "two", "three"]) {
    threads.set(id, thread(id));
    await item.follow(thread(id));
  }
  await item.refresh();
  lookups = [];
  threads.set("two", thread("two", 1));
  item.invalidate("two");
  item.invalidate("two");
  item.invalidate("not-followed");
  await item.refresh(false);
  expect(lookups).toEqual([["two"]]);
  expect(item.isUnread("two")).toBe(true);
});
