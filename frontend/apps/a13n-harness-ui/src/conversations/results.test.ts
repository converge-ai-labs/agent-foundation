// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createTransport, type Schema } from "../transport/client";
import { ResultTracker } from "./results";
import { savedResultVisible } from "./result-visibility";

let threads: Map<string, Schema<"ThreadSummary">>;
let lookups: string[][];
let reads: Schema<"ThreadReadReceipt">[][];
let failLookup: boolean;
let failRead: boolean;
let cleanups: (() => void)[];
function thread(
  id: string,
  version = 1,
  unread = true,
  readVersion = version,
): Schema<"ThreadSummary"> {
  return {
    thread_id: id,
    parent_thread_id: null,
    title: id,
    archived: false,
    unread,
    read_state_version: readVersion,
    created_at: "2026-09-16T00:00:00Z",
    updated_at: "2026-09-16T00:00:00Z",
    metadata_version: 1,
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
function tracker() {
  return new ResultTracker(createTransport("test", () => {}));
}
beforeEach(() => {
  threads = new Map();
  lookups = [];
  reads = [];
  cleanups = [];
  failLookup = false;
  failRead = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (url.pathname === "/api/threads/read") {
        const body = await request.json();
        reads.push(body.threads);
        if (failRead) throw new TypeError("Offline");
        for (const receipt of body.threads) {
          const row = threads.get(receipt.thread_id);
          if (
            row?.unread &&
            row.read_state_version === receipt.read_state_version &&
            (row.completion?.version ?? 0) === receipt.completion_version
          )
            threads.set(row.thread_id, {
              ...row,
              unread: false,
              read_state_version: (row.read_state_version ?? 0) + 1,
            });
        }
        return Response.json({
          threads: body.threads.flatMap(
            (receipt: Schema<"ThreadReadReceipt">) =>
              threads.has(receipt.thread_id)
                ? [threads.get(receipt.thread_id)]
                : [],
          ),
          total: body.threads.length,
        });
      }
      if (failLookup) throw new TypeError("Offline");
      if (url.pathname === "/api/threads/lookup") {
        const { thread_ids } = await request.json();
        lookups.push(thread_ids);
        return Response.json({
          threads: thread_ids.flatMap((id: string) =>
            threads.has(id) ? [threads.get(id)] : [],
          ),
          total: thread_ids.length,
        });
      }
      const start = Number(url.searchParams.get("cursor") ?? 0);
      const rows = [...threads.values()].filter(
        (row) => row.unread && !row.archived,
      );
      return Response.json({
        threads: rows.slice(start, start + 100),
        total: rows.length,
        next_cursor: start + 100 < rows.length ? String(start + 100) : null,
      });
    }),
  );
});
afterEach(() => {
  cleanups.forEach((stop) => stop());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("discovers unopened results across devices and reloads without browser storage", async () => {
  threads.set("unopened", thread("unopened"));
  const first = tracker();
  const second = tracker();
  await first.refresh();
  await second.refresh();
  expect(second.isUnread("unopened")).toBe(true);
  await first.acknowledge("unopened", 1, 1);
  await second.refresh();
  expect(second.isUnread("unopened")).toBe(false);
  const reopened = tracker();
  await reopened.refresh();
  expect(reopened.isUnread("unopened")).toBe(false);
  threads.set("manual", thread("manual", 0, true, 4));
  await reopened.refresh();
  await reopened.acknowledge("manual", 0, 4);
  expect(reopened.isUnread("manual")).toBe(false);
});

it("fences stale transcript and manual generations, and never regresses reading state", async () => {
  const item = tracker();
  threads.set("one", thread("one", 2));
  await item.refresh();
  await item.acknowledge("one", 1, 2);
  expect(reads).toHaveLength(0);
  const observed = item.getSnapshot().threads.get("one")!.thread;
  threads.set("one", thread("one", 2, true, 3)); // Manual mark while old read is in flight.
  await item.acknowledgeAll([observed]);
  expect(item.isUnread("one")).toBe(true);
  await item.acknowledge("one", 2, 3);
  expect(item.isUnread("one")).toBe(false);
  item.observe(thread("one", 2, true, 2), Date.now() + 1000);
  expect(item.isUnread("one")).toBe(false);
  expect(item.getSnapshot().threads.get("one")!.thread.read_state_version).toBe(
    4,
  );
  item.observe(thread("one", 3, true, 5), 0);
  expect(item.isUnread("one")).toBe(true);
  expect(
    item.getSnapshot().threads.get("one")!.thread.completion?.version,
  ).toBe(3);
});

it("batches discovery and bulk reads above 256 roots, preserving later results", async () => {
  for (let i = 0; i < 260; i++)
    threads.set(`thread-${i}`, thread(`thread-${i}`));
  const item = tracker();
  await item.refresh();
  expect(item.getSnapshot().threads.size).toBe(260);
  const selected = [...item.getSnapshot().threads.values()].map(
    (value) => value.thread,
  );
  threads.set("thread-0", thread("thread-0", 2));
  await item.acknowledgeAll(selected);
  expect(reads.map((batch) => batch.length)).toEqual([100, 100, 60]);
  expect(item.isUnread("thread-0")).toBe(true);
  expect(item.isUnread("thread-259")).toBe(false);
  const calls = reads.length;
  await item.acknowledge("thread-259", 1, 2);
  expect(reads).toHaveLength(calls);
});

it("deduplicates simultaneous visibility reports and retains failed read intent for retry", async () => {
  const item = tracker();
  threads.set("one", thread("one"));
  await item.refresh();
  await Promise.all(
    Array.from({ length: 30 }, () => item.acknowledge("one", 1, 1)),
  );
  expect(reads).toHaveLength(1);
  threads.set("one", thread("one", 2, true, 3));
  await item.refresh();
  failRead = true;
  await item.acknowledge("one", 2, 3);
  expect(item.isUnread("one")).toBe(true);
  expect(item.getSnapshot().mutationError).toContain("could not be saved");
  threads.set("one", thread("one", 3, true, 4));
  failRead = false;
  await item.refresh();
  expect(item.isUnread("one")).toBe(true);
  expect(item.getSnapshot().mutationError).toBe("");
});

it("coalesces dirty identities including never-opened roots and reconciles deletes", async () => {
  const item = tracker();
  threads.set("unopened", thread("unopened"));
  for (let i = 0; i < 30; i++) item.invalidate("unopened");
  await item.refresh(false);
  expect(lookups).toEqual([["unopened"]]);
  expect(item.isUnread("unopened")).toBe(true);
  failLookup = true;
  await item.refresh();
  expect(item.getSnapshot().lookupError).toContain("retained");
  expect(item.isUnread("unopened")).toBe(true);
  failLookup = false;
  threads.delete("unopened");
  await item.refresh();
  expect(item.getSnapshot().threads.has("unopened")).toBe(false);
});

it("processes hints arriving during an in-flight refresh", async () => {
  const item = tracker();
  threads.set("one", thread("one"));
  const original = globalThis.fetch;
  let release!: () => void;
  const paused = new Promise<void>((resolve) => {
    release = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      await paused;
      return original(request);
    }),
  );
  const refreshing = item.refresh();
  item.invalidate("one");
  release();
  await refreshing;
  expect(lookups).toEqual([["one"]]);
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
