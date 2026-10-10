import type { ReactNode } from "react";
import { Button } from "a13n-ui";
import { WarningCircle } from "@phosphor-icons/react";
import { useOperation } from "./queries";
import type { FocusDisplay } from "./stream";
import type { Schema } from "../transport/client";
import styles from "./conversation.module.css";

export function FailureNotice({
  message,
  title = "Could not finish this operation",
  action,
}: {
  message?: string | null;
  title?: string;
  action?: ReactNode;
}) {
  if (!message) return null;
  const first = message.split("\n")[0].trim();
  const short = first.length > 240 ? `${first.slice(0, 240)}…` : first;
  return (
    <section role="alert" className={styles.failureNotice}>
      <WarningCircle aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <p>{short || "The operation failed."}</p>
        {message.trim() !== short && (
          <details>
            <summary>Error details</summary>
            <pre>{message}</pre>
          </details>
        )}
        {action}
      </div>
    </section>
  );
}

export function RootFailureNotice({
  threadId,
  receipt,
  display,
  lastExecution,
  continuationId,
  completedContinuationId,
  retry,
  retryDisabled = false,
}: {
  threadId: string;
  receipt?: string | null;
  display: FocusDisplay;
  lastExecution?: Schema<"ThreadExecution"> | null;
  continuationId?: string | null;
  completedContinuationId?: string | null;
  retry?: () => void;
  retryDisabled?: boolean;
}) {
  const operation = useOperation(threadId, receipt);
  const snapshot = display.snapshot?.root_operation;
  const current =
    operation.data ??
    (snapshot?.receipt.receipt_id === receipt ? snapshot : undefined);
  const durable =
    lastExecution ?? display.snapshot?.thread.thread.last_execution;
  // Receipts are process-local and can lag a newer attempt from another App.
  // Conversely, a newly admitted operation can precede the Thread query refresh.
  const differentAttempt =
    durable && current && durable.execution_id !== current.receipt.receipt_id;
  const newerOperation =
    differentAttempt &&
    Date.parse(current.receipt.submitted_at) > Date.parse(durable.submitted_at);
  const retained = newerOperation ? undefined : durable;
  const superseded = differentAttempt && !newerOperation;
  const live =
    !superseded && display.runId && current?.run_id === display.runId
      ? display.terminalFailure
      : undefined;
  const saved =
    !superseded && current?.status === "failed"
      ? (current.failure?.message ??
        current.outcome?.execution.failure?.message ??
        current.outcome?.continuation.failure?.message ??
        "The operation could not finish.")
      : undefined;
  // A failure can leave either its own checkpoint or the previous completed
  // result selected. Neither is retry authority after context clearing.
  const retryContext =
    current?.outcome?.continuation.continuation_id ?? completedContinuationId;
  const contextChanged =
    continuationId !== undefined && continuationId !== (retryContext ?? null);
  return (
    <FailureNotice
      title={
        retained?.status === "unknown" ? "Execution outcome unknown" : undefined
      }
      message={
        live ??
        saved ??
        (retained?.status === "failed" || retained?.status === "unknown"
          ? retained.error_message
          : undefined)
      }
      action={
        !superseded &&
        current?.status === "failed" &&
        retry &&
        !contextChanged ? (
          <Button variant="outline" disabled={retryDisabled} onClick={retry}>
            Retry
          </Button>
        ) : undefined
      }
    />
  );
}
