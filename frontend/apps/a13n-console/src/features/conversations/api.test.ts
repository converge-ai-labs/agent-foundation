import { expect, it } from "vitest";
import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { createClient } from "../../service-client";
import {
  applyThreadState,
  conversationKeys,
  conversationQueries,
  invalidateConversation,
} from "./api";
import {
  fixtureRun,
  fixtureRunPage,
  fixtureSession,
  fixtureNavigationPage,
  fixtureThread,
} from "./transcript/fixture";

it("updates loaded summaries without refetching history and never regresses a streamed version", async () => {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const original = fixtureRun({
    status: "running",
    sealed_at: null,
    version: 2,
  });
  const thread = fixtureThread({
    current_run_id: original.id,
    last_run_id: null,
    version: 3,
  });
  const keys = conversationKeys("ws_1");
  const requests: URL[] = [];
  const client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch: async (request) => {
      requests.push(new URL((request as Request).url));
      return Response.json(fixtureRunPage([original]));
    },
  });
  const queries = conversationQueries(client, "ws_1");
  const first = queries.runs(thread.id, { anchor: original.id });
  const older = queries.runs(thread.id, { cursor: "older" });
  cache.setQueryData(keys.thread(thread.id), thread);
  cache.setQueryData(first.queryKey, fixtureRunPage([original]));
  cache.setQueryData(
    older.queryKey,
    fixtureRunPage([fixtureRun({ id: "ancestor" })], {
      previous_cursor: "newer",
    }),
  );
  const changed = { ...original, version: 5, attempts: 2 };
  applyThreadState(cache, "ws_1", {
    thread: { ...thread, version: 4 },
    current_run: changed,
    last_run: null,
    entries: [],
  });
  expect(cache.getQueryData(first.queryKey)?.items[0]).toMatchObject({
    version: 5,
    attempts: 2,
  });
  expect(cache.getQueryState(first.queryKey)?.isInvalidated).toBe(false);
  expect(cache.getQueryState(older.queryKey)?.isInvalidated).toBe(false);
  await cache.fetchQuery(first); // A stale HTTP read loses to the newer stream.
  expect(cache.getQueryData(first.queryKey)?.items[0]?.version).toBe(5);
  applyThreadState(cache, "ws_1", {
    thread,
    current_run: original,
    last_run: null,
    entries: [],
  });
  expect(cache.getQueryData(keys.run(original.id))).toMatchObject({
    version: 5,
  });
  const successor = fixtureRun({
    id: "successor",
    status: "accepted",
    version: 1,
  });
  applyThreadState(cache, "ws_1", {
    thread: {
      ...thread,
      version: 6,
      current_run_id: successor.id,
      last_run_id: original.id,
    },
    current_run: successor,
    last_run: changed,
    entries: [],
  });
  expect(cache.getQueryState(first.queryKey)?.isInvalidated).toBe(true);
  expect(cache.getQueryState(older.queryKey)?.isInvalidated).toBe(false);
  expect(requests).toHaveLength(1);
  cache.clear();
  client.close();
});

it("hydrates initial stream snapshots without refetching settled opening reads, then refreshes new membership and usage", async () => {
  const cache = new QueryClient({
    defaultOptions: { queries: { staleTime: 15_000, retry: false } },
  });
  const requested: string[] = [];
  const thread = fixtureThread();
  const run = fixtureRun();
  const client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch: async (input) => {
      const url = new URL((input as Request).url);
      requested.push(url.pathname);
      return Response.json(fixtureNavigationPage([thread]));
    },
  });
  const queries = conversationQueries(client, "ws_1");
  const keys = conversationKeys("ws_1");
  const navigation = queries.threadPage(thread.session_id, { limit: 1 });
  cache.setQueryData(keys.session(thread.session_id), fixtureSession());
  cache.setQueryData(keys.run(run.id), run);
  cache.setQueryData(
    queries.runs(thread.id, { anchor: run.id }).queryKey,
    fixtureRunPage([run]),
  );
  cache.setQueryData(navigation.queryKey, fixtureNavigationPage([thread]));
  cache.setQueryData(keys.usage({ session_id: thread.session_id }), {
    models: [],
  });
  cache.setQueryData(keys.usage({ run_id: run.id }), { models: [] });
  const observer = new QueryObserver(cache, navigation);
  const unsubscribe = observer.subscribe(() => undefined);
  applyThreadState(cache, "ws_1", {
    thread,
    current_run: null,
    last_run: run,
    entries: [],
  });
  applyThreadState(cache, "ws_1", {
    thread: fixtureThread({ id: "old_child", origin: "child" }),
    current_run: null,
    last_run: null,
    entries: [],
  });
  expect(
    cache.getQueryState(keys.session(thread.session_id))?.isInvalidated,
  ).toBe(false);
  expect(
    cache.getQueryState(queries.runs(thread.id, { anchor: run.id }).queryKey)
      ?.isInvalidated,
  ).toBe(false);
  expect(
    cache.getQueryState(keys.usage({ session_id: thread.session_id }))
      ?.isInvalidated,
  ).toBe(false);
  expect(requested).toEqual([]);
  applyThreadState(cache, "ws_1", {
    thread: { ...thread, version: thread.version + 1 },
    current_run: null,
    last_run: { ...run, version: run.version + 1 },
    entries: [],
  });
  expect(
    cache.getQueryState(keys.usage({ session_id: thread.session_id }))
      ?.isInvalidated,
  ).toBe(true);
  expect(
    cache.getQueryState(keys.usage({ run_id: run.id }))?.isInvalidated,
  ).toBe(true);
  applyThreadState(cache, "ws_1", {
    thread: fixtureThread({
      id: "new_fork",
      origin: "fork",
      current_run_id: "new_run",
    }),
    current_run: fixtureRun({
      id: "new_run",
      thread_id: "new_fork",
      created_at: "2026-09-21T10:00:00Z",
    }),
    last_run: null,
    entries: [],
  });
  expect(
    cache.getQueryState(keys.session(thread.session_id))?.isInvalidated,
  ).toBe(true);
  expect(requested).toEqual(["/api/v1/sessions/ses_1/threads"]);
  unsubscribe();
  cache.clear();
  client.close();
});

it("compares acceptance timestamps numerically when fractional seconds differ", () => {
  const cache = new QueryClient();
  const keys = conversationKeys("ws_1");
  cache.setQueryData(
    keys.session("ses_1"),
    fixtureSession({
      updated_at: "2026-09-21T10:00:00Z",
    }),
  );
  applyThreadState(cache, "ws_1", {
    thread: fixtureThread(),
    current_run: fixtureRun({ created_at: "2026-09-21T10:00:00.100Z" }),
    last_run: null,
    entries: [],
  });
  expect(cache.getQueryState(keys.session("ses_1"))?.isInvalidated).toBe(true);
  cache.clear();
});

it("refreshes metric rankings and only affected history pages when a streamed Run seals", async () => {
  const cache = new QueryClient();
  const client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
  });
  const queries = conversationQueries(client, "ws_1");
  const keys = conversationKeys("ws_1");
  const run = fixtureRun({ status: "running", sealed_at: null });
  const thread = fixtureThread({ current_run_id: run.id, last_run_id: null });
  const current = queries.runs(thread.id, { anchor: run.id });
  const older = queries.runs(thread.id, { cursor: "older" });
  const ranked = queries.rankedRuns(thread.id, "cost", "desc");
  const ranking = { pages: [fixtureRunPage([run])], pageParams: [undefined] };
  cache.setQueryData(keys.thread(thread.id), thread);
  cache.setQueryData(keys.run(run.id), run);
  cache.setQueryData(current.queryKey, fixtureRunPage([run]));
  cache.setQueryData(
    older.queryKey,
    fixtureRunPage([fixtureRun({ id: "old" })], { previous_cursor: "newer" }),
  );
  cache.setQueryData(ranked.queryKey, ranking);

  const sealed = fixtureRun({ version: run.version + 1 });
  applyThreadState(cache, "ws_1", {
    thread: {
      ...thread,
      current_run_id: null,
      last_run_id: run.id,
      version: thread.version + 1,
    },
    current_run: null,
    last_run: sealed,
    entries: [],
  });
  expect(cache.getQueryData(ranked.queryKey)).toEqual(ranking);
  expect(cache.getQueryState(ranked.queryKey)?.isInvalidated).toBe(true);
  expect(cache.getQueryState(current.queryKey)?.isInvalidated).toBe(true);
  expect(cache.getQueryState(older.queryKey)?.isInvalidated).toBe(false);
  expect(cache.getQueryData(current.queryKey)?.items[0]).toMatchObject({
    status: "completed",
    version: sealed.version,
  });

  cache.setQueryData(ranked.queryKey, ranking);
  applyThreadState(cache, "ws_1", {
    thread: { ...thread, version: thread.version + 2, current_run_id: "next" },
    current_run: fixtureRun({
      id: "next",
      status: "accepted",
      sealed_at: null,
    }),
    last_run: sealed,
    entries: [],
  });
  expect(cache.getQueryState(ranked.queryKey)?.isInvalidated).toBe(true);
  cache.setQueryData(ranked.queryKey, ranking);
  await invalidateConversation(cache, "ws_1", { threadId: thread.id });
  expect(cache.getQueryState(ranked.queryKey)?.isInvalidated).toBe(true);
  cache.clear();
  client.close();
});

it("uses a bounded ordinary Thread query for delegation links", async () => {
  const requested: URL[] = [];
  const client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch: async (request) => {
      requested.push(new URL((request as Request).url));
      return Response.json({ items: [], next_cursor: null });
    },
  });
  const cache = new QueryClient();
  await cache.fetchQuery(
    conversationQueries(client, "ws_1").threads("ses_1", {
      origin: ["child"],
      origin_run_id: "run_2",
      limit: 1,
    }),
  );
  expect(requested).toHaveLength(1);
  expect(requested[0]!.pathname).toBe("/api/v1/threads");
  expect(Object.fromEntries(requested[0]!.searchParams)).toEqual({
    session_id: "ses_1",
    origin: "child",
    origin_run_id: "run_2",
    limit: "1",
  });
  cache.clear();
  client.close();
});
