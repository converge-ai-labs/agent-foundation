import { WarningCircle } from "@phosphor-icons/react";
import { useOperation } from "./queries";
import type { FocusDisplay } from "./stream";
import styles from "./conversation.module.css";

export function FailureNotice({ message }: { message?: string | null }) {
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
      </div>
    </section>
  );
}

export function RootFailureNotice({
  threadId,
  receipt,
  display,
}: {
  threadId: string;
  receipt?: string | null;
  display: FocusDisplay;
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
  return <FailureNotice message={live ?? saved} />;
}
