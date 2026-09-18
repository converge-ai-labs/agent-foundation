import { Button } from "a13n-ui";
import { SquareIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  commandHeaders,
  workspaceHeaders,
  data,
  type Schema,
} from "../../shared/api";
import { ErrorNotice, ErrorToast } from "../../shared/feedback";
import { useIdempotency } from "../../shared/idempotency";
import {
  conversationQueries,
  invalidateConversation,
  isActiveRun,
  runPath,
} from "./api";
import { Composer } from "./composer";
import { ContinueWithoutFeedback } from "./continuation";
import { RunFeedback } from "./feedback";
import { PendingFeedback } from "./pending";
import { ThreadQueue } from "./queue";
import { useRun } from "./queries";
import styles from "./conversations.module.css";

export function RunControls({
  run,
  thread,
  configuration,
}: {
  run: Schema["RunResource"];
  thread: Schema["ThreadResource"];
  configuration?: {
    composer: ReactNode;
    accepted: (receipt: Schema["RunAcceptanceReceipt"]) => void;
  };
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can, basePath } = useWorkspace(),
    cache = useQueryClient(),
    navigate = useNavigate();
  const currentRun = useRun(thread.current_run_id);
  const headRun = useRun(thread.head_run_id);
  const current = thread.current_run_id === run.id;
  const active = isActiveRun(run.status);
  const waiting =
    current && thread.head_run_id === run.id && run.status === "waiting";
  const debug = thread.session_purpose === "debug" && thread.role === "root";
  const interactive = !!configuration || debug;
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
          params: {
            path: { run_id: run.id, steer_id: steer!.steer_id },
          },
          headers: workspaceHeaders(workspace.id),
        })
        .then(data),
    refetchInterval: (query) =>
      query.state.data?.status === "pending" ? 1500 : false,
  });
  const interruptKey = useIdempotency(),
    retryKey = useIdempotency();
  function refresh() {
    return invalidateConversation(cache, workspace.id, {
      sessionId: run.session_id,
      threadId: thread.id,
      runId: run.id,
    });
  }
  function accepted(receipt: Schema["RunAcceptanceReceipt"]) {
    void refresh();
    if (configuration) configuration.accepted(receipt);
    else navigate(runPath(basePath, receipt));
  }
  const interrupt = useMutation({
    mutationFn: () => {
      const body = {
        expected_thread_version: thread.version,
        expected_run_version: run.version,
      };
      return client.http
        .POST("/api/v1/runs/{run_id}/interrupt", {
          params: {
            path: { run_id: run.id },
            header: commandHeaders(workspace.id, interruptKey.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: () => {
      interruptKey.reset();
      void refresh();
    },
    onError: () => void refresh(),
  });
  const retry = useMutation({
    mutationFn: () => {
      const body = { expected_thread_version: thread.version };
      return client.http
        .POST("/api/v1/runs/{run_id}/retry", {
          params: {
            path: { run_id: run.id },
            header: commandHeaders(workspace.id, retryKey.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: (receipt) => {
      retryKey.reset();
      accepted(receipt);
    },
    onError: () => void refresh(),
  });
  const canContinue =
    can("run.continue") &&
    can("agent.invoke") &&
    headRun.data?.status === "completed";
  return (
    <>
      <ErrorToast error={interrupt.error ?? retry.error} />
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
              />
            ) : (
              <PendingFeedback actions={pending.data.items} />
            ))}
          {interactive && can("run.continue") && can("run.feedback") && (
            <ContinueWithoutFeedback
              run={run}
              thread={thread}
              accepted={accepted}
            />
          )}
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
      <div className={styles.composerDock}>
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
            <div className={styles.dockControls}>
              {debug && (
                <span className={styles.debugHint}>
                  {t("Debug session")}
                  {" · "}
                  {t(
                    active
                      ? "Guide the current run"
                      : waiting
                        ? "Waiting for feedback"
                        : "Continue testing this agent",
                  )}
                </span>
              )}
              {active && can("run.interrupt") && (
                <Button
                  size="sm"
                  variant="ghost"
                  loading={interrupt.isPending}
                  onClick={() => interrupt.mutate()}
                >
                  <SquareIcon size={13} />
                  {t("Stop")}
                </Button>
              )}
              {interactive &&
                ["failed", "cancelled"].includes(run.status) &&
                can("run.retry") && (
                  <Button
                    size="sm"
                    variant="outline"
                    loading={retry.isPending}
                    onClick={() => retry.mutate()}
                  >
                    {t("Retry run")}
                  </Button>
                )}
            </div>
            {debug && !configuration && !waiting && (
              <Composer
                label={t(active ? "Send guidance" : "Run next step")}
                placeholder={t(
                  active
                    ? "Add guidance to the current run…"
                    : "Enter the next test input…",
                )}
                disabled={active ? !can("run.steer") : !canContinue}
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
              <p className={styles.notice}>
                {t(
                  "This session is controlled by its originating application. Use Try agent to start a separate debug session.",
                )}
              </p>
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
