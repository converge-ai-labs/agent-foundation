import { expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import type { Schema } from "../transport/client";
import { invalidateWorkRead, readCurrentWork } from "./work";

const key = ["thread", "root", "work", "summary"];
function observation(run: string): Schema<"ThreadWork"> {
  return {
    thread_id: "root",
    epoch: "epoch",
    sequence: 1,
    source: "live",
    run_id: run,
    tasks: {},
    notes: {},
    children: {},
  };
}

it("discards a delayed old Run response and joins hints arriving during a read", async () => {
  const client = new QueryClient();
  client.setQueryData(key, observation("previous"));
  let release!: (value: Schema<"ThreadWork">) => void;
  const first = new Promise<Schema<"ThreadWork">>((resolve) => {
    release = resolve;
  });
  const read = vi
    .fn()
    .mockReturnValueOnce(first)
    .mockResolvedValue(observation("current"));
  const seen: unknown[] = [];
  const unsubscribe = client
    .getQueryCache()
    .subscribe(() =>
      seen.push(client.getQueryData<Schema<"ThreadWork">>(key)?.run_id),
    );
  const pending = client.fetchQuery({
    queryKey: key,
    queryFn: ({ signal }) => readCurrentWork(client, key, signal, read),
  });
  const query = client.getQueryCache().find({ queryKey: key })!;
  invalidateWorkRead(query);
  invalidateWorkRead(query);
  release(observation("old-run"));
  expect((await pending).run_id).toBe("current");
  expect(read).toHaveBeenCalledTimes(2);
  expect(seen).not.toContain("old-run");
  unsubscribe();
  client.clear();
});

it("keeps last content on failure and does not publish a cancelled read", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(key, observation("retained"));
  await expect(
    client.fetchQuery({
      queryKey: key,
      queryFn: ({ signal }) =>
        readCurrentWork(client, key, signal, async () => {
          throw new Error("Disconnected");
        }),
    }),
  ).rejects.toThrow("Disconnected");
  expect(client.getQueryData<Schema<"ThreadWork">>(key)?.run_id).toBe(
    "retained",
  );
  let release!: (value: Schema<"ThreadWork">) => void;
  const read = new Promise<Schema<"ThreadWork">>((resolve) => {
    release = resolve;
  });
  const pending = client.fetchQuery({
    queryKey: key,
    queryFn: ({ signal }) => readCurrentWork(client, key, signal, () => read),
  });
  await client.cancelQueries({ queryKey: key });
  release(observation("cancelled"));
  // TanStack cancellation reverts to the prior cached value.
  expect((await pending).run_id).toBe("retained");
  expect(client.getQueryData<Schema<"ThreadWork">>(key)?.run_id).toBe(
    "retained",
  );
  client.clear();
});
