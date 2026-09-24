import { afterEach, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import type { Schema } from "../transport/client";
import { applyActivityUpdates } from "./activity-updates";
import {
  applyThreadMutation,
  mergeThreadSelections,
  retainThreadSelections,
} from "./thread-updates";

const thread: Schema<"ThreadSummary"> = {
  thread_id: "one",
  created_at: "2026-09-01",
  updated_at: "2026-09-01",
  title: "Before",
  metadata_version: 1,
  archived: false,
  starred: true,
  continuation_state: "initial",
  root_activity: { state: "inactive" },
  configuration: {
    version: 1,
    agent_source: { kind: "agent", id: "agent-one" },
    environment_profile_id: "environment-native",
  },
};

afterEach(() => vi.useRealTimers());

it("merges independent versions without rolling back activity or unrelated threads", () => {
  const current = {
    ...thread,
    metadata_version: 3,
    title: "Latest title",
    root_activity: { state: "running" as const, run_id: "run-one" },
  };
  const updated = {
    ...thread,
    configuration: {
      ...thread.configuration,
      version: 2,
      default_model_id: "model-two",
    },
  };
  const merged = mergeThreadSelections(current, updated);
  expect(merged.title).toBe("Latest title");
  expect(merged.configuration.default_model_id).toBe("model-two");
  expect(merged.root_activity).toBe(current.root_activity);
  expect(mergeThreadSelections(merged, thread)).toBe(merged);
  expect(
    mergeThreadSelections(current, { ...updated, thread_id: "other" }),
  ).toBe(current);
});

it("publishes confirmed fields in detail and loaded navigation without invalidating history", () => {
  const client = new QueryClient();
  const detail = { thread, continuation_id: "C1", available_actions: ["run"] };
  const listKey = ["threads", "", "project-one"];
  const historyKey = ["thread", "one", "history", "C1"];
  client.setQueryData(["thread", "one", "detail"], detail);
  client.setQueryData(listKey, {
    pages: [
      {
        rows: [{ thread, latest_activity: { text: "Keep activity" } }],
        active_rows: [],
        starred_rows: [
          { thread, latest_activity: { text: "Keep star activity" } },
        ],
        total: 1,
        next_cursor: "next",
        observedAt: 12,
      },
    ],
    pageParams: [undefined],
  });
  client.setQueryData(historyKey, {
    pages: [{ entries: ["saved"] }],
    pageParams: [undefined],
  });
  const history = client.getQueryData(historyKey);
  const updated = { ...thread, title: "Renamed", metadata_version: 2 };
  applyThreadMutation(client, updated);
  expect(
    client.getQueryData<Schema<"ThreadDetail">>(["thread", "one", "detail"])
      ?.thread.title,
  ).toBe("Renamed");
  expect(client.getQueryData(listKey)).toMatchObject({
    pages: [
      {
        rows: [
          {
            thread: { title: "Renamed" },
            latest_activity: { text: "Keep activity" },
          },
        ],
        starred_rows: [
          {
            thread: { title: "Renamed", starred: true, metadata_version: 2 },
            latest_activity: { text: "Keep star activity" },
          },
        ],
        next_cursor: "next",
        observedAt: 12,
      },
    ],
  });
  expect(client.getQueryData(historyKey)).toBe(history);
  expect(client.getQueryState(historyKey)?.isInvalidated).toBe(false);
  // Reads that began before the mutation retain its confirmed versions while
  // accepting independently newer execution observations.
  expect(
    retainThreadSelections(client, {
      ...thread,
      starred: false,
      root_activity: { state: "running", run_id: "run-two" },
    }),
  ).toMatchObject({
    title: "Renamed",
    metadata_version: 2,
    starred: true,
    root_activity: { run_id: "run-two" },
  });
  client.clear();
});

it.each([false, true])(
  "reconciles collection membership when a configuration response also changes starred from %s",
  async (wasStarred) => {
    vi.useFakeTimers();
    const client = new QueryClient();
    const old = { ...thread, starred: wasStarred };
    const key = ["threads", "", "", false, "all"];
    const unrelatedKey = ["threads", "", "unrelated", false, "all"];
    client.setQueryData(key, {
      pages: [
        {
          rows: wasStarred ? [] : [{ thread: old }],
          starred_rows: wasStarred ? [{ thread: old }] : [],
          total: 1,
        },
      ],
      pageParams: [undefined],
    });
    client.setQueryData(unrelatedKey, {
      pages: [{ rows: [], starred_rows: [], total: 0 }],
      pageParams: [undefined],
    });
    const updated = {
      ...old,
      starred: !wasStarred,
      metadata_version: 2,
      configuration: {
        ...old.configuration,
        version: 2,
        default_model_id: "model-two",
      },
    };
    applyThreadMutation(client, updated);
    // The lookup now observes already-patched metadata. It must not consume the
    // membership transition carried by the independent configuration response.
    applyActivityUpdates(
      client,
      [{ thread: updated } as Schema<"ThreadActivityView">],
      [thread.thread_id],
    );
    await vi.advanceTimersByTimeAsync(200);
    expect(client.getQueryState(key)?.isInvalidated).toBe(true);
    expect(client.getQueryState(unrelatedKey)?.isInvalidated).toBe(false);
    client.clear();
  },
);
