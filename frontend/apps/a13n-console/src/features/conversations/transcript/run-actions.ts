import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../../shared/api";
import { useIdempotency } from "../../../shared/idempotency";
import { invalidateConversation, runPath } from "../api";

export interface ConfigurationBridge {
  composer: React.ReactNode;
  accepted: (receipt: Schema["RunAcceptanceReceipt"]) => void;
}

/** Only a debug root thread, or the configuration assistant, may steer a run. */
export function isInteractive(
  thread: Schema["ThreadResource"],
  configuration?: ConfigurationBridge,
) {
  return (
    !!configuration ||
    (thread.session_purpose === "debug" && thread.role === "root")
  );
}

/**
 * Where an accepted command lands: the configuration assistant keeps its own
 * navigation, every other caller follows the receipt to its run.
 */
export function useRunAcceptance(
  run: Schema["RunResource"],
  thread: Schema["ThreadResource"],
  configuration?: ConfigurationBridge,
) {
  const cache = useQueryClient(),
    navigate = useNavigate(),
    { workspace, basePath } = useWorkspace();
  const refresh = () =>
    invalidateConversation(cache, workspace.id, {
      sessionId: run.session_id,
      threadId: thread.id,
      runId: run.id,
    });
  return {
    refresh,
    accepted(receipt: Schema["RunAcceptanceReceipt"]) {
      void refresh();
      if (configuration) configuration.accepted(receipt);
      else navigate(runPath(basePath, receipt));
    },
  };
}

export function useRetryRun(
  run: Schema["RunResource"],
  thread: Schema["ThreadResource"],
  accepted: (receipt: Schema["RunAcceptanceReceipt"]) => void,
  refresh: () => unknown,
) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    key = useIdempotency();
  return useMutation({
    mutationFn: () => {
      const body = { expected_thread_version: thread.version };
      return client.http
        .POST("/api/v1/runs/{run_id}/retry", {
          params: {
            path: { run_id: run.id },
            header: commandHeaders(workspace.id, key.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: (receipt) => {
      key.reset();
      accepted(receipt);
    },
    onError: () => void refresh(),
  });
}

export function useInterruptRun(
  run: Schema["RunResource"],
  thread: Schema["ThreadResource"],
  refresh: () => unknown,
) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    key = useIdempotency();
  return useMutation({
    mutationFn: () => {
      const body = {
        expected_thread_version: thread.version,
        expected_run_version: run.version,
      };
      return client.http
        .POST("/api/v1/runs/{run_id}/interrupt", {
          params: {
            path: { run_id: run.id },
            header: commandHeaders(workspace.id, key.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: () => {
      key.reset();
      void refresh();
    },
    onError: () => void refresh(),
  });
}
