import { useQuery } from "@tanstack/react-query";
import { useCallback, useMemo, useSyncExternalStore } from "react";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { conversationQueries } from "./api";
import { isOmitted, type DisplayItem } from "./display";
import { useEarlierItems } from "./earlier-items";
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
const unsubscribed = () => () => undefined;

/**
 * A Run's Items and execution, read from one display so they never disagree.
 * The session stream's display of the Run, while it holds one, carries its
 * live output; any other Run is read as committed. Items before the display's
 * first are read on request.
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
  const read = useQuery({ ...queries.items(runId), enabled: !held });
  const attempts = useQuery(queries.attempts(runId)).data ?? NO_ATTEMPTS;
  const shown = useMemo(() => held ?? committed(read.data), [held, read.data]);
  const earlier = useEarlierItems(runId, shown?.first);
  const all = useMemo(
    () => [...earlier.items, ...(shown?.items ?? [])],
    [earlier.items, shown],
  );
  const items = useMemo(() => presentItems(all), [all]);
  const execution = useMemo<RunExecution>(
    () =>
      run && shown
        ? {
            ...runExecution(run, all),
            // Unread or missing Items take their execution facts with them.
            coverage: shown.partial || earlier.more ? "partial" : "complete",
          }
        : { ...emptyExecution(), coverage: "partial" },
    [run, shown, all, earlier.more],
  );
  const closed = stream?.state === "closed" && !shown?.complete;
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
    earlier,
    error: closed ? stream.error : read.error,
    reconnect: closed ? stream.reconnect : () => void read.refetch(),
  };
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
