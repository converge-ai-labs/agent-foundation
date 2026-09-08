import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Dialog, Select } from "a13n-ui";
import { GitFork, RefreshCw, Square } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { useIdempotency } from "../../shared/idempotency";
import { commandHeaders, data, type Schema } from "../../shared/api";
import {
  ErrorNotice,
  Loading,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { Confirm, JsonView } from "../../shared/form";
import { conversationApi, isActiveRun, runPath } from "./api";
import { useLiveRun } from "./live";
import { InputContent, PresentedItems } from "./items";
import { Composer } from "./composer";
import { OptionsComposer } from "./options";
import { PendingFeedback } from "./pending";
import { ThreadQueue } from "./queue";
import { RunInspector } from "./inspector";
import { SteeringStatus } from "./steer";
import { HistoryTranscript } from "./history";
import { ContinueBranch, ContinueWithoutFeedback } from "./branches";
import { MessageMarkdown } from "./markdown";
import styles from "./conversations.module.css";

export function RunPage() {
  const { runId = "", threadId = "", sessionId = "" } = useParams();
  return (
    <RunContent
      key={runId}
      runId={runId}
      threadId={threadId}
      sessionId={sessionId}
    />
  );
}
function RunContent({
  runId,
  threadId,
  sessionId,
}: {
  runId: string;
  threadId: string;
  sessionId: string;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can } = useWorkspace(),
    cache = useQueryClient(),
    navigate = useNavigate(),
    api = conversationApi(client, workspace.id);
  const live = useLiveRun(runId),
    [mode, setMode] = useState("message"),
    [notice, setNotice] = useState(""),
    [steerIds, setSteerIds] = useState<string[]>([]);
  const runQuery = useQuery({
    queryKey: ["run", workspace.id, runId],
    queryFn: ({ signal }) => api.run(runId, signal),
  });
  const run = runQuery.data;
  const threadQuery = useQuery({
    queryKey: ["thread", workspace.id, run?.thread_id],
    enabled: !!run,
    queryFn: ({ signal }) => api.thread(run!.thread_id, signal),
  });
  const pending = useQuery({
    queryKey: ["pending-actions", workspace.id, runId],
    queryFn: ({ signal }) => api.pending(runId, signal),
  });
  const thread = threadQuery.data;
  const currentRun = useQuery({
    queryKey: ["run", workspace.id, thread?.current_run_id],
    enabled: !!thread?.current_run_id,
    queryFn: ({ signal }) => api.run(thread!.current_run_id!, signal),
  });
  const headRun = useQuery({
    queryKey: ["run", workspace.id, thread?.head_run_id],
    enabled: !!thread?.head_run_id,
    queryFn: ({ signal }) => api.run(thread!.head_run_id!, signal),
  });
  const retry = useMutation({
    mutationFn: () =>
      client.http
        .POST("/api/v1/runs/{run_id}/retry", {
          params: {
            path: { run_id: runId },
            header: commandHeaders(
              workspace.id,
              retryKey.forBody({ expected_thread_version: thread!.version }),
            ),
          },
          body: { expected_thread_version: thread!.version },
        })
        .then(data),
    onSuccess: accepted,
  });
  const retryKey = useIdempotency();
  function accepted(receipt: Schema["RunAcceptanceReceipt"]) {
    void cache.invalidateQueries();
    navigate(runPath(workspace.id, receipt));
  }
  if (runQuery.isPending || threadQuery.isPending) return <Loading />;
  if (!run || !thread)
    return (
      <ErrorNotice
        error={runQuery.error ?? threadQuery.error}
        retry={() => {
          void runQuery.refetch();
          void threadQuery.refetch();
        }}
      />
    );
  if (run.thread_id !== threadId || run.session_id !== sessionId)
    return (
      <ErrorNotice
        error={new Error(t("This run does not belong to this thread."))}
      />
    );
  const current = thread.current_run_id === runId,
    active = isActiveRun(run.status),
    waiting =
      current && thread.head_run_id === runId && run.status === "waiting";
  const steer = current && (active || waiting) && can("run.steer");
  return (
    <div className={styles.run}>
      <header className={styles.runHeader}>
        <div>
          <StateBadge state={run.status} />
          <span>
            <Timestamp value={run.created_at} />
          </span>
        </div>
        <div className={styles.inline}>
          <RunInspector run={run} />
          {run.status === "completed" &&
            currentRun.data &&
            !isActiveRun(currentRun.data.status) &&
            can("run.continue") && (
              <ContinueBranch run={run} thread={thread} accepted={accepted} />
            )}
          {current && active && can("run.interrupt") && (
            <Confirm
              title={t("Interrupt run")}
              description={t(
                "Stop the current execution. Its retained output remains in this conversation.",
              )}
              trigger={
                <>
                  <Square size={13} />
                  {t("Interrupt")}
                </>
              }
              danger
              action={() =>
                client.http
                  .POST("/api/v1/runs/{run_id}/interrupt", {
                    params: {
                      path: { run_id: runId },
                      header: commandHeaders(workspace.id, crypto.randomUUID()),
                    },
                    body: {
                      expected_thread_version: thread.version,
                      expected_run_version: run.version,
                    },
                  })
                  .then(data)
              }
            />
          )}
          {current &&
            ["failed", "cancelled"].includes(run.status) &&
            can("run.retry") && (
              <Button
                loading={retry.isPending}
                size="sm"
                icon={<RefreshCw size={13} />}
                onClick={() => retry.mutate()}
              >
                {t("Retry run")}
              </Button>
            )}
          {run.status === "completed" && can("run.fork") && (
            <Dialog
              title={t("Fork conversation")}
              description={t(
                "Start a new thread from this completed run with the same agent configuration.",
              )}
              closeLabel={t("Close")}
              trigger={
                <Button size="sm" icon={<GitFork size={13} />}>
                  {t("Fork")}
                </Button>
              }
            >
              <OptionsComposer
                label={t("Fork and send")}
                submit={async (intent, key) =>
                  accepted(
                    data(
                      await client.http.POST("/api/v1/runs/{run_id}/fork", {
                        params: {
                          path: { run_id: runId },
                          header: commandHeaders(workspace.id, key),
                        },
                        body: intent,
                      }),
                    ),
                  )
                }
              />
            </Dialog>
          )}
        </div>
      </header>
      <ErrorNotice error={runQuery.error ?? threadQuery.error ?? retry.error} />
      {live.gap && (
        <p role="status" className={styles.notice}>
          {t(
            "Live replay had a gap. Available retained items have been reconciled; some live-only output may be unavailable.",
          )}
        </p>
      )}
      {live.state === "disconnected" && (
        <ErrorNotice
          error={live.error ?? new Error(t("Live connection disconnected."))}
          retry={live.reconnect}
        />
      )}
      <div className={styles.transcript}>
        <HistoryTranscript runId={runId} />
        <article className={styles.inputMessage}>
          <strong>
            {t(run.input_kind === "feedback" ? "Your responses" : "You")}
          </strong>
          <InputContent input={run.input} fallback={run.input_text} />
        </article>
        <PresentedItems items={live.items} runState={run.status} />
        {!live.items.some(
          (item) =>
            item.kind === "text_message" &&
            item.role === "assistant" &&
            item.text,
        ) &&
          run.output_text && (
            <article className={styles.message}>
              <div className={styles.messageBody}>
                <strong>{t("Assistant")}</strong>
                <MessageMarkdown text={run.output_text} />
              </div>
            </article>
          )}
        {active && (
          <p className={styles.liveStatus} role="status">
            {t(
              live.state === "connected"
                ? "Agent is working…"
                : "Connecting to run…",
            )}
          </p>
        )}
        {run.failure != null && (
          <section className={styles.failure}>
            <h3>{t("Run failure")}</h3>
            <JsonView value={run.failure} />
          </section>
        )}
        {run.output != null && (
          <details>
            <summary>{t("Structured output")}</summary>
            <JsonView value={run.output} />
          </details>
        )}
      </div>
      {waiting && run.sealed_state_digest_sha256 && (
        <>
          <ErrorNotice error={pending.error} />
          {pending.data && (
            <PendingFeedback
              key={run.sealed_state_digest_sha256}
              run={run}
              thread={thread}
              actions={pending.data.items}
            />
          )}
          {can("run.continue") && can("run.feedback") && (
            <ContinueWithoutFeedback
              run={run}
              thread={thread}
              accepted={accepted}
            />
          )}
        </>
      )}
      {steerIds.map((id) => (
        <SteeringStatus key={id} runId={runId} steerId={id} />
      ))}
      {notice && (
        <p role="status" className={styles.notice}>
          {notice}
        </p>
      )}
      {current && can("run.continue") && (
        <>
          <Select
            label={t("Send mode")}
            placeholder={t("Send mode")}
            value={steer ? mode : "message"}
            onValueChange={setMode}
            options={[
              {
                value: "message",
                label: t(
                  active || waiting ? "Queue next message" : "Continue thread",
                ),
              },
              ...(steer
                ? [{ value: "steer", label: t("Steer current run") }]
                : []),
            ]}
          />
          {mode === "steer" && steer ? (
            <Composer
              label={t("Send steering")}
              submit={async (input, key) => {
                const receipt = data(
                  await client.http.POST("/api/v1/runs/{run_id}/steer", {
                    params: {
                      path: { run_id: runId },
                      header: commandHeaders(workspace.id, key),
                    },
                    body: input,
                  }),
                );
                setSteerIds((previous) => [...previous, receipt.steer_id]);
              }}
            />
          ) : (
            <OptionsComposer
              commandBasis={thread.version}
              label={t(active || waiting ? "Add to queue" : "Send")}
              submit={async (intent, key) => {
                const receipt = data(
                  await client.http.POST("/api/v1/threads/{thread_id}/runs", {
                    params: {
                      path: { thread_id: thread.id },
                      header: commandHeaders(workspace.id, key),
                    },
                    body: {
                      ...intent,
                      expected_thread_version: thread.version,
                    },
                  }),
                );
                if (receipt.run) accepted(receipt.run);
                else {
                  setNotice(t("Message added to the thread queue."));
                  void cache.invalidateQueries();
                }
              }}
            />
          )}
        </>
      )}
      {!current && (
        <p className={styles.notice}>
          {t("You are viewing a historical run.")}{" "}
          {thread.current_run_id && (
            <Link
              to={runPath(workspace.id, {
                session_id: thread.session_id,
                thread_id: thread.id,
                run_id: thread.current_run_id,
              })}
            >
              {t("Open current run")}
            </Link>
          )}
        </p>
      )}
      <ThreadQueue
        thread={thread}
        canConsume={
          (!thread.current_run_id ||
            (!!currentRun.data &&
              !isActiveRun(currentRun.data.status) &&
              currentRun.data.status !== "waiting")) &&
          (!thread.head_run_id ||
            (!!headRun.data && headRun.data.status !== "waiting"))
        }
      />
    </div>
  );
}
