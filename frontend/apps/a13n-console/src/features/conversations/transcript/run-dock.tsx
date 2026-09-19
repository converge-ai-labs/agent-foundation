import { Button } from "a13n-ui";
import { SquareIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import {
  commandHeaders,
  data,
  workspaceHeaders,
  type Schema,
} from "../../../shared/api";
import { ErrorNotice, ErrorToast } from "../../../shared/feedback";
import { conversationQueries, isActiveRun, runPath } from "../api";
import { Composer } from "../composer";
import { useRun } from "../queries";
import { ContinueWithoutFeedback } from "./continuation";
import { PendingRequests, RunFeedback } from "./pending-request";
import { ThreadQueue } from "./queue";
import {
  isInteractive,
  useInterruptRun,
  useRunAcceptance,
  type ConfigurationBridge,
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
  configuration,
  above,
}: {
  run: Schema["RunResource"];
  thread: Schema["ThreadResource"];
  agentName?: string;
  configuration?: ConfigurationBridge;
  /** Floating controls that belong just above the composer. */
  above?: ReactNode;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can, basePath } = useWorkspace();
  const currentRun = useRun(thread.current_run_id);
  const headRun = useRun(thread.head_run_id);
  const current = thread.current_run_id === run.id;
  const active = isActiveRun(run.status);
  const waiting =
    current && thread.head_run_id === run.id && run.status === "waiting";
  const interactive = isInteractive(thread, configuration);
  const debug = interactive && !configuration;
  const pending = useQuery({
    ...conversationQueries(client, workspace.id).pending(run.id),
    enabled: waiting,
  });
  const [steer, setSteer] = useState<Schema["SteerReceipt"]>();
  const steerStatus = useQuery({
    queryKey: ["steer", workspace.id, run.id, steer?.steer_id],
    enabled: !!steer,
    queryFn: () =>
      client.http
        .GET("/api/v1/runs/{run_id}/steers/{steer_id}", {
          params: { path: { run_id: run.id, steer_id: steer!.steer_id } },
          headers: workspaceHeaders(workspace.id),
        })
        .then(data),
    refetchInterval: (query) =>
      query.state.data?.status === "pending" ? 1500 : false,
  });
  const { accepted, refresh } = useRunAcceptance(run, thread, configuration);
  const interrupt = useInterruptRun(run, thread, refresh);
  const canContinue =
    can("run.continue") &&
    can("agent.invoke") &&
    headRun.data?.status === "completed";
  const stop = active && can("run.interrupt") && (
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
      {waiting && (
        <>
          <ErrorNotice
            error={pending.error}
            retry={() => void pending.refetch()}
          />
          {pending.data &&
            (interactive && can("run.feedback") ? (
              <RunFeedback
                key={run.sealed_state_digest_sha256}
                run={run}
                thread={thread}
                actions={pending.data.items}
                accepted={accepted}
                continuation={
                  can("run.continue") && (
                    <ContinueWithoutFeedback
                      run={run}
                      thread={thread}
                      accepted={accepted}
                    />
                  )
                }
              />
            ) : (
              <PendingRequests actions={pending.data.items} />
            ))}
        </>
      )}
      {!configuration && (
        <ThreadQueue
          thread={thread}
          canConsume={
            !!currentRun.data &&
            !isActiveRun(currentRun.data.status) &&
            currentRun.data.status !== "waiting" &&
            (!thread.head_run_id ||
              (!!headRun.data && headRun.data.status !== "waiting"))
          }
        />
      )}
      <div className={styles.dock}>
        {above}
        {!current ? (
          <p className={styles.notice}>
            {t("You are viewing a historical run.")}{" "}
            {thread.current_run_id && (
              <Link
                to={runPath(basePath, {
                  session_id: thread.session_id,
                  thread_id: thread.id,
                  run_id: thread.current_run_id,
                })}
              >
                {t("Open current run")}
              </Link>
            )}
          </p>
        ) : (
          <>
            {debug && !waiting && (
              <Composer
                agentName={agentName}
                label={t(active ? "Send guidance" : "Run next step")}
                placeholder={t(
                  active
                    ? "Add guidance to the current run…"
                    : "Enter the next test input…",
                )}
                disabled={active ? !can("run.steer") : !canContinue}
                leading={stop}
                submit={async (input, key) => {
                  try {
                    if (active) {
                      setSteer(
                        data(
                          await client.http.POST(
                            "/api/v1/runs/{run_id}/steer",
                            {
                              params: {
                                path: { run_id: run.id },
                                header: commandHeaders(workspace.id, key),
                              },
                              body: input,
                            },
                          ),
                        ),
                      );
                    } else {
                      // Exact-source Continue cannot silently queue if another caller advances this Thread.
                      accepted(
                        data(
                          await client.http.POST(
                            "/api/v1/runs/{source_run_id}/continue",
                            {
                              params: {
                                path: { source_run_id: thread.head_run_id! },
                                header: commandHeaders(workspace.id, key),
                              },
                              body: {
                                input,
                                expected_thread_version: thread.version,
                              },
                            },
                          ),
                        ),
                      );
                    }
                  } finally {
                    void refresh();
                  }
                }}
              />
            )}
            {debug && !active && !waiting && !thread.head_run_id && (
              <p className={styles.notice}>
                {t(
                  "There is no completed state to continue. Retry this run or start a new debug session.",
                )}
              </p>
            )}
            {!interactive && (
              <div className={styles.readOnly}>
                <p className={styles.notice}>
                  {t(
                    "This session is controlled by its originating application. Use New session to start your own debug session.",
                  )}
                </p>
                {stop}
              </div>
            )}
          </>
        )}
        {steer && (
          <p className={styles.notice} role="status">
            {t(
              steerStatus.data?.status === "consumed"
                ? "Guidance applied to the run."
                : steerStatus.data?.status === "superseded"
                  ? "The run ended before guidance was applied."
                  : "Guidance accepted. Waiting for the agent to apply it.",
            )}
          </p>
        )}
        <ErrorNotice
          error={steerStatus.error}
          retry={() => void steerStatus.refetch()}
        />
        {configuration?.composer}
      </div>
    </>
  );
}
