import { expect, it } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { createClient } from "../../service-client";
import { applyThreadState, conversationKeys, conversationQueries } from "./api";
import {
  fixtureRun,
  fixtureRunPage,
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
