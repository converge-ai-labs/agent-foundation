import { useEffect, useState } from "react";
import { ReplayGapError } from "@converge.ai/a13n";
import { revalidateSession, useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { applyExecution, type Execution } from "./execution";

type ExecutionRead =
  | { status: "loading" | "unavailable" }
  | { status: "streaming" | "ready"; execution: Execution }
  | { status: "error"; error: unknown };

export function useExecution(runId: string, enabled: boolean) {
  const client = useClient();
  const { workspace } = useWorkspace();
  const [state, setState] = useState<ExecutionRead>({ status: "loading" });
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    setState({ status: "loading" });
    async function read() {
      let execution: Execution = { steps: [], items: new Map() };
      try {
        for await (const entry of client.streamRun(runId, {
          workspaceId: workspace.id,
          signal: controller.signal,
        })) {
          if (controller.signal.aborted) return;
          execution = applyExecution(execution, entry);
          if (
            [
              "run.completed",
              "run.failed",
              "run.cancelled",
              "run.waiting",
            ].includes(entry.event.event_type)
          ) {
            setState({ status: "ready", execution });
            return;
          }
          setState({ status: "streaming", execution });
        }
        // EOF without a terminal observation cannot establish a complete step count.
        if (!controller.signal.aborted) setState({ status: "unavailable" });
      } catch (error) {
        if (controller.signal.aborted) return;
        revalidateSession(error);
        setState(
          error instanceof ReplayGapError
            ? { status: "unavailable" }
            : { status: "error", error },
        );
      }
    }
    void read();
    return () => controller.abort();
  }, [client, workspace.id, runId, enabled, revision]);
  return { state, retry: () => setRevision((value) => value + 1) };
}
