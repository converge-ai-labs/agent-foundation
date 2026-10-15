import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { conversationQueries } from "./api";
import { isOmitted, type DisplayItem, type FullDisplay } from "./display";
import {
  emptyExecution,
  runExecution,
  type Execution,
  type ExecutionCoverage,
} from "./execution";
import { presentItems } from "./projection";
import type { HeldDisplay } from "./session-displays";
import { useSessionStream } from "./session-stream";

export interface RunExecution extends Execution {
  coverage: ExecutionCoverage;
}

const NO_ATTEMPTS: Schema["AttemptView"][] = [];
const NO_ITEMS: DisplayItem[] = [];
const unsubscribed = () => () => undefined;

/**
 * A Run's Items and execution, read from one display so they never disagree.
 * The session stream's display of the Run, while it holds one, carries its
 * live output; any other Run is read as committed. Every committed Item of
 * the Run is loaded: the Items before a held display's first come from the
 * Run's full committed read.
 */
export function useRunDisplay(runId: string) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    queries = conversationQueries(client, workspace.id),
    stream = useSessionStream(),
    displays = stream?.displays;
  const held = useSyncExternalStore(
    useCallback(
      (listener: () => void) =>
        displays?.subscribe(runId, listener) ?? unsubscribed(),
      [displays, runId],
    ),
    () => displays?.get(runId),
  );
  const run = useQuery(queries.run(runId)).data;
  const read = useQuery(queries.items(runId));
  const attempts = useQuery(queries.attempts(runId)).data ?? NO_ATTEMPTS;
  const shown = useMemo(() => held ?? committed(read.data), [held, read.data]);
  const before = held?.first ?? 1;
  const earlier = useMemo(
    () =>
      held && read.data
        ? (read.data.items as DisplayItem[]).filter(
            (item) => item.ordinal < before,
          )
        : NO_ITEMS,
    [held, read.data, before],
  );
  // The committed read covers the Items before a held display only once each
  // was final when read: it is read again as the held display moves on.
  const covered = !held || before <= 1 || finalBefore(read.data, before);
  const { refetch, isFetching } = read;
  useEffect(() => {
    if (!covered && !isFetching) void refetch();
    // Only a held display that moves on asks again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [before, covered]);
  const all = useMemo(
    () => [...earlier, ...(shown?.items ?? [])],
    [earlier, shown],
  );
  const items = useMemo(() => presentItems(all), [all]);
  const execution = useMemo<RunExecution>(
    () =>
      run && shown
        ? {
            ...runExecution(run, all),
            // Unread or missing Items take their execution facts with them.
            coverage: shown.partial || !covered ? "partial" : "complete",
          }
        : { ...emptyExecution(), coverage: "partial" },
    [run, shown, all, covered],
  );
  const closed = stream?.state === "closed" && !shown?.complete;
  const error = closed ? stream.error : read.error;
  return {
    items,
    displayItems: all,
    execution,
    attempts,
    /** The session stream is live. */
    connected: stream?.state === "live",
    /** Saved or live output is missing. */
    gap: shown?.partial ?? false,
    /** Content the saved display omitted. */
    incomplete: shown?.omitted ?? false,
    loading: !shown && !error,
    error,
    reconnect: closed ? stream.reconnect : () => void read.refetch(),
  };
}

/** Whether every Item before `first` was final when the committed read returned it. */
function finalBefore(read: FullDisplay | undefined, first: number) {
  if (!read) return false;
  const final = read.complete ? Infinity : read.immutableBefore;
  let expected = 1;
  for (const item of read.items) {
    if (item.ordinal >= first) break;
    if (item.ordinal !== expected || item.ordinal >= final) return false;
    expected += 1;
  }
  return expected >= first;
}

function committed(read?: Schema["RunItems"]): HeldDisplay | undefined {
  if (!read) return undefined;
  const items = read.items as DisplayItem[];
  const omitted = items.some((item) => isOmitted(item.content));
  return {
    items,
    first: items[0]?.ordinal ?? 1,
    complete: read.complete,
    partial: omitted,
    omitted,
  };
}
