import { Button, ModalFrame } from "a13n-ui";
import {
  GitBranchIcon,
  LockSimpleIcon,
  SquareIcon,
} from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../../shared/api";
import { useIdempotency } from "../../../shared/idempotency";
import { ExecutionFields, executionOverride } from "../../../shared/forms";
import { ErrorNotice, ErrorToast } from "../../../shared/feedback";
import {
  conversationQueries,
  isActiveRun,
  runPath,
  type ViewLevel,
} from "../api";
import { ForkRun } from "../fork-run";
import { Composer } from "../composer";
import { useRun } from "../queries";
import type { Resubmission } from "../resubmit";
import { DesktopAssistance } from "./desktop-assistance";
import { ContinueWithoutFeedback } from "./continuation";
import { PendingRequests, RunFeedback } from "./pending-request";
import { ThreadInbox } from "./inbox";
import {
  isInteractive,
  useInterruptRun,
  useRunAcceptance,
} from "./run-actions";
import debug from "./debug/debug.module.css";
import styles from "./transcript.module.css";

export interface PendingMessage {
  payload: Schema["MessagePayload"];
  /** Keep the optimistic message until navigation displays this accepted Run. */
  runId?: string;
}

/**
 * The end of the transcript: what the run is waiting for, what is queued behind
 * it, and the one place a person can answer or continue.
 */
export function RunDock({
  run,
  thread,
  agentName,
  above,
  resubmit,
  onResubmitted,
  onSubmissionChange,
  pendingRunId,
  level = "chat",
}: {
  run: Schema["RunView"];
  thread: Schema["ThreadView"];
  agentName?: string;
  /** Floating controls that belong just above the composer. */
  above?: ReactNode;
  /** A stopped Run's message, prefilled to be sent again. */
  resubmit?: Resubmission | null;
  onResubmitted?: () => void;
  onSubmissionChange?: (message: PendingMessage | null) => void;
  /** A receipt can update the Thread before the route transition commits. */
  pendingRunId?: string;
  level?: ViewLevel;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { search } = useLocation(),
    { workspace, can, basePath } = useWorkspace();
  // The Thread's latest Run: the active one, else the one sealed last.
  const idempotency = useIdempotency();
  const [execution, setExecution] = useState<Schema["ExecutionOverride"]>({});
  const [executionOpen, setExecutionOpen] = useState(false);
  const latest = thread.current_run_id ?? thread.last_run_id;
  const latestRun = useRun(latest);
  const following = new URLSearchParams(search).get("follow") === "latest";
  const current =
    following ||
    latest === run.id ||
    (!!pendingRunId && latest === pendingRunId);
  const active = isActiveRun(run.status);
  // Only the Thread's last Run can wait; show its exact questions.
  const last = latest === run.id ? run : latestRun.data;
  const waiting =
    !thread.current_run_id && current && last?.status === "waiting"
      ? last
      : null;
  const interactive = isInteractive(thread);
  // After a failed or cancelled Run the inbox advances only when someone
  // starts its next message.
  const canRunNext =
    !!latestRun.data && ["failed", "cancelled"].includes(latestRun.data.status);
  // Guidance is an inbox entry; its own status says whether the run took it,
  // and it is read again whenever the Thread reports a change.
  const [guidance, setGuidance] = useState<{
    runId: string;
    entry: Schema["EntryView"];
  }>();
  const steer = guidance?.runId === run.id ? guidance.entry : undefined;
  const steerStatus = useQuery({
    ...conversationQueries(client, workspace.id).entry(
      thread.id,
      steer?.id ?? "",
    ),
    enabled: !!steer,
  });
  const entry = steerStatus.data ?? steer;
  const { accepted, refresh } = useRunAcceptance(run, thread);
  const interrupt = useInterruptRun(run, refresh);
  const canStop = active && can("run");
  const stop = canStop && (
    <Button
      size="icon-sm"
      variant="ghost"
      className={styles.stopButton}
      aria-label={t("Stop")}
      title={t("Stop")}
      loading={interrupt.isPending}
      onClick={() => interrupt.mutate()}
    >
      <SquareIcon size={13} weight="fill" />
    </Button>
  );
  const feedback =
    waiting &&
    waiting.pending &&
    (waiting.pending.calls.length > 0 ||
      waiting.pending.approvals.length > 0) &&
    (interactive && can("run") ? (
      <RunFeedback
        key={waiting.id}
        run={waiting}
        pending={waiting.pending}
        accepted={accepted}
        continuation={
          <ContinueWithoutFeedback
            run={waiting}
            thread={thread}
            accepted={accepted}
          />
        }
      />
    ) : (
      <PendingRequests pending={waiting.pending} />
    ));
  return (
    <>
      <ErrorToast error={interrupt.error} />
      {current &&
        interactive &&
        can("run") &&
        !run.pending?.calls.some(
          (call) => call.tool_name === "request_desktop_assistance",
        ) &&
        run.environment_mounts.some((mount) =>
          mount.permission_ceiling?.operations?.includes(
            "environment.computer.observe",
          ),
        ) && (
          <DesktopAssistance
            key={run.id}
            runId={run.id}
            mounts={run.environment_mounts
              .filter((mount) =>
                mount.permission_ceiling?.operations?.includes(
                  "environment.computer.observe",
                ),
              )
              .map((mount) => mount.name)}
            canContinue={run.wait_reason === "desktop_control"}
            onContinued={accepted}
            onRefresh={refresh}
          />
        )}

      {/* Debug answers a wait in a card beside the runs. */}
      {feedback && level === "debug" ? (
        <div className={debug.feedback}>{feedback}</div>
      ) : (
        feedback
      )}
      <ThreadInbox
        thread={thread}
        canRunNext={canRunNext}
        quiet={level === "chat"}
        className={level === "debug" ? debug.inbox : undefined}
      />
      <div className={styles.dock}>
        {above}
        {!current ? (
          <div className={styles.dockNotice}>
            <GitBranchIcon size={13} aria-hidden="true" />
            <span>{t("You are viewing earlier work.")}</span>
            <ForkRun run={run} thread={thread} level={level} compact />
            {latest && (
              <Link
                className={styles.dockLink}
                to={`${runPath(basePath, {
                  session_id: thread.session_id,
                  thread_id: thread.id,
                  run_id: latest,
                })}?${new URLSearchParams({ ...Object.fromEntries(new URLSearchParams(search)), follow: "latest" })}`}
              >
                {t(
                  level === "debug"
                    ? "Return to latest run"
                    : "Return to latest messages",
                )}
              </Link>
            )}
          </div>
        ) : (
          <>
            {interactive && !waiting && (
              <Composer
                key={resubmit ? "resubmit" : "message"}
                initial={resubmit?.payload}
                agentName={agentName}
                label={t(
                  active
                    ? "Add guidance"
                    : level === "debug"
                      ? "Start run"
                      : "Send message",
                )}
                placeholder={
                  active
                    ? t("Add guidance while it works")
                    : level === "debug"
                      ? t("Input for the next run…")
                      : agentName
                        ? t("Message {{agent}}…", { agent: agentName })
                        : t("Message your agent…")
                }
                disabled={!can("run") || (following && latest !== run.id)}
                options={
                  !active &&
                  !resubmit && (
                    <ModalFrame
                      open={executionOpen}
                      onOpenChange={setExecutionOpen}
                      title={t("Run execution policy")}
                      description={t("Changes apply to the next run only.")}
                      closeLabel={t("Close")}
                      trigger={
                        <Button type="button" variant="ghost" size="sm">
                          {t("Execution policy")}
                        </Button>
                      }
                      footer={
                        <Button
                          type="button"
                          onClick={() => setExecutionOpen(false)}
                        >
                          {t("Apply")}
                        </Button>
                      }
                    >
                      <ExecutionFields
                        value={execution}
                        onChange={setExecution}
                      />
                    </ModalFrame>
                  )
                }
                stop={canStop ? () => interrupt.mutate() : undefined}
                stopping={interrupt.isPending}
                submit={async (payload) => {
                  if (!active) onSubmissionChange?.({ payload });
                  try {
                    const policy =
                      !active && !resubmit
                        ? executionOverride(execution)
                        : undefined;
                    const body: Schema["Message"] = {
                      kind: "message",
                      delivery: active ? "steer" : "next_run",
                      payload,
                      agent_id: run.agent_id,
                      ...(policy ? { options: { execution: policy } } : {}),
                      ...(resubmit && {
                        agent_revision_id: resubmit.agent_revision_id,
                        options: resubmit.options,
                      }),
                    };
                    // Guidance joins the active run; a next step never joins
                    // a run another caller started meanwhile.
                    const receipt = data(
                      await client
                        .workspace(workspace.id)
                        .POST("/api/v1/threads/{thread_id}/inbox", {
                          params: {
                            path: {
                              thread_id: thread.id,
                            },
                            header: commandHeaders(
                              idempotency.forBody({
                                threadId: thread.id,
                                body,
                              }),
                            ),
                          },
                          body,
                        }),
                    );
                    idempotency.reset();
                    if (!active) setExecution({});
                    if (resubmit) onResubmitted?.();
                    if (receipt.run) {
                      if (!active)
                        onSubmissionChange?.({
                          payload,
                          runId: receipt.run.id,
                        });
                      accepted(receipt.run, receipt.thread);
                    } else {
                      if (active)
                        setGuidance({ runId: run.id, entry: receipt.entry });
                      else onSubmissionChange?.(null);
                      void refresh();
                    }
                  } catch (error) {
                    if (!active) onSubmissionChange?.(null);
                    void refresh();
                    throw error;
                  }
                }}
              />
            )}
            {!interactive && (
              <div className={styles.dockNotice}>
                <LockSimpleIcon size={13} aria-hidden="true" />
                <span>
                  {t(
                    "This thread is driven by the run that delegated to it. Continue the work from the parent thread.",
                  )}
                </span>
                {stop}
              </div>
            )}
          </>
        )}
        {entry && (
          <p className={styles.dockReceipt} role="status">
            {t(
              entry.status === "consumed" && entry.assigned_run_id === run.id
                ? "Guidance applied."
                : entry.status === "failed" ||
                    (!!entry.assigned_run_id &&
                      entry.assigned_run_id !== run.id)
                  ? "The work ended before guidance was applied."
                  : "Guidance accepted. Waiting for the agent to apply it.",
            )}
          </p>
        )}
        <ErrorNotice
          error={steerStatus.error}
          retry={() => void steerStatus.refetch()}
        />
      </div>
    </>
  );
}
