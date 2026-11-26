import { useTranslation } from "../i18n";
import type { ReactNode } from "react";
import { Button } from "a13n-ui";
import { Link } from "react-router";
import { WarningCircle } from "@phosphor-icons/react";
import { useOperation } from "./queries";
import type { FocusDisplay } from "./stream";
import type { Schema } from "../transport/client";
import styles from "./conversation.module.css";

export function FailureNotice({
  message,
  title,
  action,
  code,
  threadId,
  runId,
  executionId,
  details,
}: {
  message?: string | null;
  title?: string;
  action?: ReactNode;
  code?: string | null;
  threadId?: string;
  runId?: string | null;
  executionId?: string;
  details?: Record<string, unknown> | null;
}) {
  const { t } = useTranslation();
  if (!message) return null;
  const first = message.split("\n")[0].trim();
  const short = first.length > 240 ? `${first.slice(0, 240)}…` : first;
  const diagnostic = [
    message,
    threadId && `${t("Thread ID")}: ${threadId}`,
    runId && `${t("Run ID")}: ${runId}`,
    executionId && `${t("Execution ID")}: ${executionId}`,
    details &&
      Object.keys(details).length > 0 &&
      JSON.stringify(details, null, 2),
  ]
    .filter(Boolean)
    .join("\n");
  return (
    <section role="alert" className={styles.failureNotice}>
      <WarningCircle aria-hidden="true" />
      <div>
        <strong>{title ?? t("Could not finish this operation")}</strong>
        <p>{short || t("The operation failed.")}</p>
        {code && (
          <p>
            {t("Error code")}: {code}
          </p>
        )}
        {diagnostic.trim() !== short && (
          <details>
            <summary>{t("Error details")}</summary>
            <pre tabIndex={0}>{diagnostic}</pre>
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
  acpAuthPath,
}: {
  threadId: string;
  receipt?: string | null;
  display: FocusDisplay;
  lastExecution?: Schema<"ThreadExecution"> | null;
  continuationId?: string | null;
  completedContinuationId?: string | null;
  retry?: () => void;
  retryDisabled?: boolean;
  acpAuthPath?: string;
}) {
  const { t } = useTranslation();
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
  const settled =
    !superseded && current?.status === "failed" ? current : undefined;
  const savedFailure =
    settled?.failure ??
    settled?.outcome?.execution.failure ??
    settled?.outcome?.continuation.failure;
  const retainedFailure =
    retained?.status === "failed" || retained?.status === "unknown"
      ? retained
      : undefined;
  // A failure can leave either its own checkpoint or the previous completed
  // result selected. Neither is retry authority after context clearing.
  const retryContext =
    current?.outcome?.continuation.continuation_id ?? completedContinuationId;
  const contextChanged =
    continuationId !== undefined && continuationId !== (retryContext ?? null);
  const failureCode =
    savedFailure?.code ??
    retainedFailure?.error_code ??
    (live ? display.terminalFailureCode : undefined);
  return (
    <FailureNotice
      title={
        retained?.status === "unknown"
          ? t("Execution outcome unknown")
          : undefined
      }
      message={
        // Finalization adds Host failure classification and the diagnostic report.
        // A generic RUN_ERROR must not hide that richer settled result.
        savedFailure?.message ??
        retainedFailure?.error_message ??
        live ??
        (settled ? t("The operation could not finish.") : undefined)
      }
      code={failureCode}
      threadId={threadId}
      runId={
        settled?.run_id ??
        retainedFailure?.run_id ??
        (live ? display.runId : undefined)
      }
      executionId={settled?.receipt.receipt_id ?? retainedFailure?.execution_id}
      details={savedFailure?.details}
      action={
        <>
          {failureCode === "acp_auth_required" && acpAuthPath && (
            <Button variant="outline" render={<Link to={acpAuthPath} />}>
              {t("Open Agent authentication")}
            </Button>
          )}
          {!superseded &&
            (current?.status === "failed" ||
              failureCode === "acp_auth_required") &&
            retry &&
            !contextChanged && (
              <Button
                variant="outline"
                disabled={retryDisabled}
                onClick={retry}
              >
                {t("Retry")}
              </Button>
            )}
        </>
      }
    />
  );
}
