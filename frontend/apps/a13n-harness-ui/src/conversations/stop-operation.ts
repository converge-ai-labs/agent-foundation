import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import type { ThreadDraft } from "./draft";

export type StopRequest = {
  receipt: string;
  attempt: number;
  phase: "requesting" | "waiting" | "uncertain" | "rejected";
  message: string;
  outcome?: Schema<"RootOperationStatus">;
};

export function useStopOperation(
  threadId: string,
  draft: ThreadDraft,
  activity: Schema<"RootActivityView">,
  reconcile: () => void,
) {
  const transport = useTransport();
  // Keep the request in the private Thread draft across navigation. A late
  // response belongs to its exact receipt, never to a replacement operation.
  const request =
    activity.state !== "inactive" && draft.stop?.receipt === activity.receipt_id
      ? draft.stop
      : undefined;
  const operation = useQuery({
    // Each deliberate attempt needs a new observation. Cached running data
    // (or an in-flight read) from before this POST cannot authorize another retry.
    queryKey: ["thread", threadId, "stop", request?.receipt, request?.attempt],
    enabled: !!request && request.phase !== "requesting" && !request.outcome,
    queryFn: async ({ signal }) => {
      const value = await result(
        transport.client.GET("/api/operations/{receipt_id}", {
          params: { path: { receipt_id: request!.receipt } },
          signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]),
        }),
      );
      if (
        value.receipt.receipt_id !== request!.receipt ||
        value.receipt.thread_id !== threadId
      )
        throw new Error("Operation status did not match the Stop request.");
      return value;
    },
    retry: false,
    // Only an explicit, unresolved Stop needs this confirmation fallback. SSE
    // still owns ordinary refreshes; never retry the cancellation POST itself.
    refetchInterval: (query) =>
      !request || request.phase === "requesting" || request.outcome
        ? false
        : query.state.error
          ? 5000
          : 1000,
  });
  useEffect(() => {
    const status = operation.data?.status;
    if (
      !request ||
      request.phase === "requesting" ||
      draft.stop !== request ||
      request.outcome ||
      !status ||
      status === "preparing" ||
      status === "running"
    )
      return;
    request.outcome = status;
    request.message =
      status === "cancelled"
        ? "Operation stopped. Refreshing conversation…"
        : `Operation ${status}. Refreshing conversation…`;
    draft.notify();
    reconcile();
  }, [draft, request, operation.data, reconcile]);

  const retryable =
    !!request &&
    !request.outcome &&
    ["uncertain", "rejected"].includes(request.phase) &&
    operation.isSuccess &&
    ["preparing", "running"].includes(operation.data.status);
  const canStop =
    activity.state !== "inactive" &&
    !!activity.receipt_id &&
    !!activity.available_actions?.includes("cancel") &&
    (!request || retryable);
  const stop = async () => {
    if (!canStop || !activity.receipt_id) return;
    // The synchronous latch excludes stale callbacks and two clicks before
    // React commits, including an acknowledgement that resolves immediately.
    if (
      draft.stop?.receipt === activity.receipt_id &&
      (draft.stop !== request || !retryable)
    )
      return;
    const next: StopRequest = {
      receipt: activity.receipt_id,
      attempt: (draft.stop?.attempt ?? 0) + 1,
      phase: "requesting",
      message: "Requesting Stop…",
    };
    draft.stop = next;
    draft.notify();
    try {
      const response = await result(
        transport.client.POST("/api/operations/{receipt_id}/cancel", {
          params: { path: { receipt_id: next.receipt } },
          signal: AbortSignal.timeout(10000),
        }),
      );
      if (
        response.receipt_id !== next.receipt ||
        typeof response.accepted !== "boolean"
      )
        throw new Error("Stop acknowledgement did not match this operation.");
      if (draft.stop !== next || next.outcome) return;
      next.phase = "waiting";
      next.message = response.accepted
        ? "Stopping… Waiting for execution and cleanup to finish."
        : "This operation already ended. Checking its final status…";
    } catch (error) {
      if (draft.stop !== next || next.outcome) return;
      const rejected =
        error instanceof ApiError && error.status >= 400 && error.status < 500;
      next.phase = rejected ? "rejected" : "uncertain";
      next.message = rejected
        ? `Stop was not accepted. ${error.message}`
        : "Stop could not be confirmed. It may already have been accepted; checking operation status. No automatic retry was sent.";
    } finally {
      draft.notify();
      reconcile();
    }
  };
  return {
    request,
    canStop,
    stop,
    retryable,
    stopping: !!request && !retryable,
    observationFailed: !!request && !request.outcome && operation.isError,
    refresh: () => {
      if (request?.phase !== "requesting") void operation.refetch();
      reconcile();
    },
  };
}
