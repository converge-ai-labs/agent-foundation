import { Button } from "a13n-ui";
import {
  GitBranchIcon,
  LockSimpleIcon,
  SquareIcon,
} from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../../shared/api";
import { ErrorNotice, ErrorToast } from "../../../shared/feedback";
import { conversationQueries, isActiveRun, runPath } from "../api";
import { Composer } from "../composer";
import { useRun } from "../queries";
import type { Resubmission } from "../resubmit";
import { ContinueWithoutFeedback } from "./continuation";
import { PendingRequests, RunFeedback } from "./pending-request";
import { ThreadInbox } from "./inbox";
import {
  isInteractive,
  questionsOnly,
  useInterruptRun,
  useRunAcceptance,
} from "./run-actions";
import styles from "./transcript.module.css";

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
}: {
  run: Schema["RunView"];
  thread: Schema["ThreadView"];
  agentName?: string;
  /** Floating controls that belong just above the composer. */
  above?: ReactNode;
  /** A stopped Run's message, prefilled to be sent again. */
  resubmit?: Resubmission | null;
  onResubmitted?: () => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can, basePath } = useWorkspace();
  // The Thread's latest Run: the active one, else the one sealed last.
  const latest = thread.current_run_id ?? thread.last_run_id;
  const latestRun = useRun(latest);
  const headRun = useRun(thread.head_run_id);
  const current = latest === run.id;
  const active = isActiveRun(run.status);
  const waiting =
    current && thread.head_run_id === run.id && run.status === "waiting";
  const interactive = isInteractive(thread);
  // After a failed or cancelled Run the inbox advances only when someone
  // starts its next message, and a head waiting for more than answers to its
  // questions still has to be resumed first.
  const canRunNext =
    !!latestRun.data &&
    ["failed", "cancelled"].includes(latestRun.data.status) &&
    (!thread.head_run_id ||
      (!!headRun.data &&
        (headRun.data.status !== "waiting" ||
          questionsOnly(headRun.data.pending?.items ?? []))));
  // Guidance is an inbox entry; its own status says whether the run took it,
  // and it is read again whenever the Thread reports a change.
  const [steer, setSteer] = useState<Schema["EntryView"]>();
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
  return (
    <>
      <ErrorToast error={interrupt.error} />
      {waiting &&
        run.pending &&
        (interactive && can("run") ? (
          <RunFeedback
            key={run.id}
            run={run}
            thread={thread}
            actions={run.pending.items}
            accepted={accepted}
            continuation={
              <ContinueWithoutFeedback
                run={run}
                thread={thread}
                accepted={accepted}
              />
            }
          />
        ) : (
          <PendingRequests actions={run.pending.items} />
        ))}
      <ThreadInbox thread={thread} canRunNext={canRunNext} />
      <div className={styles.dock}>
        {above}
        {!current ? (
          <p className={styles.dockNotice}>
            <GitBranchIcon size={13} aria-hidden="true" />
            {t("You are viewing a historical run.")}
            {latest && (
              <Link
                className={styles.dockLink}
                to={runPath(basePath, {
                  session_id: thread.session_id,
                  thread_id: thread.id,
                  run_id: latest,
                })}
              >
                {t("Open current run")}
              </Link>
            )}
          </p>
        ) : (
          <>
            {interactive && !waiting && (
              <Composer
                key={resubmit ? "resubmit" : "message"}
                initial={resubmit?.payload}
                agentName={agentName}
                label={t(active ? "Send guidance" : "Run next step")}
                placeholder={
                  active
                    ? t("Send guidance while it works")
                    : agentName
                      ? t("Message {{agent}}…", { agent: agentName })
                      : t("Message your agent…")
                }
                disabled={!can("run")}
                stop={canStop ? () => interrupt.mutate() : undefined}
                stopping={interrupt.isPending}
                submit={async (payload, key) => {
                  try {
                    // Guidance joins the active run; a next step never joins
                    // a run another caller started meanwhile.
                    const receipt = data(
                      await client.http.POST(
                        "/api/v1/workspaces/{workspace_id}/threads/{thread_id}/inbox",
                        {
                          params: {
                            path: {
                              workspace_id: workspace.id,
                              thread_id: thread.id,
                            },
                            header: commandHeaders(key),
                          },
                          body: {
                            kind: "message",
                            delivery: active ? "steer" : "next_run",
                            payload,
                            agent_id: run.agent_id,
                            ...(resubmit && {
                              agent_revision_id: resubmit.agent_revision_id,
                              options: resubmit.options,
                            }),
                          },
                        },
                      ),
                    );
                    if (resubmit) onResubmitted?.();
                    if (receipt.run) accepted(receipt.run);
                    else if (active) setSteer(receipt.entry);
                  } finally {
                    void refresh();
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
                ? "Guidance applied to the run."
                : entry.status === "failed" ||
                    (!!entry.assigned_run_id &&
                      entry.assigned_run_id !== run.id)
                  ? "The run ended before guidance was applied."
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
