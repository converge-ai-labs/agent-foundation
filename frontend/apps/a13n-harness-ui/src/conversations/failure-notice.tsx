import type { ReactNode } from "react";
import { Button } from "a13n-ui";
import { WarningCircle } from "@phosphor-icons/react";
import { useOperation } from "./queries";
import type { FocusDisplay } from "./stream";
import styles from "./conversation.module.css";

export function FailureNotice({
  message,
  action,
}: {
  message?: string | null;
  action?: ReactNode;
}) {
  if (!message) return null;
  const first = message.split("\n")[0].trim();
  const short = first.length > 240 ? `${first.slice(0, 240)}…` : first;
  return (
    <section role="alert" className={styles.failureNotice}>
      <WarningCircle aria-hidden="true" />
      <div>
        <strong>Could not finish this operation</strong>
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
  continuationId,
  completedContinuationId,
  retry,
  retryDisabled = false,
}: {
  threadId: string;
  receipt?: string | null;
  display: FocusDisplay;
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
  const live =
    display.runId && current?.run_id === display.runId
      ? display.terminalFailure
      : undefined;
  const saved =
    current?.status === "failed"
      ? (current.failure?.message ??
        current.outcome?.execution.failure?.message ??
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
      message={live ?? saved}
      action={
        current?.status === "failed" && retry && !contextChanged ? (
          <Button variant="outline" disabled={retryDisabled} onClick={retry}>
            Retry
          </Button>
        ) : undefined
      }
    />
  );
}
