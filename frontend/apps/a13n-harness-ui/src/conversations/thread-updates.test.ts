import { expect, it } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import type { Schema } from "../transport/client";
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
  continuation_state: "initial",
  root_activity: { state: "inactive" },
  configuration: {
    version: 1,
    agent_source: { kind: "agent", id: "agent-one" },
    environment_profile_id: "environment-native",
  },
};

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
      root_activity: { state: "running", run_id: "run-two" },
    }),
  ).toMatchObject({
    title: "Renamed",
    metadata_version: 2,
    root_activity: { run_id: "run-two" },
  });
  client.clear();
});
