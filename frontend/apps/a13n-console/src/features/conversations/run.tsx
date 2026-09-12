import { Button, ChoiceField, DisclosureSection, ModalFrame } from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import {
  ArrowDownIcon,
  GitForkIcon,
  ArrowsClockwiseIcon,
  SquareIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import {
  ErrorNotice,
  Loading,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { JsonView } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import {
  conversationQueries,
  invalidateConversation,
  isActiveRun,
  runPath,
} from "./api";
import { ContinueBranch, ContinueWithoutFeedback } from "./branches";
import { Composer } from "./composer";
import styles from "./conversations.module.css";
import { HistoryTranscript } from "./history";
import { RunInspector } from "./inspector";
import { InputContent, PresentedItems } from "./items";
import { useLiveRun } from "./live";
import { MarkdownContent } from "../../shared/markdown";
import { OptionsComposer } from "./options";
import { PendingFeedback } from "./pending";
import { useAgent } from "../agents/queries";
import { useRun } from "./queries";
import { ThreadQueue } from "./queue";
import { SteeringStatus } from "./steer";

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
    { workspace, can, basePath } = useWorkspace(),
    cache = useQueryClient(),
    navigate = useNavigate(),
    queries = conversationQueries(client, workspace.id);
  const live = useLiveRun(runId),
    [mode, setMode] = useState("message"),
    [notice, setNotice] = useState(""),
    [steerIds, setSteerIds] = useState<string[]>([]);
  const runQuery = useRun(runId);
  const run = runQuery.data;
  const agent = useAgent(run?.agent_id);
  const transcript = useRef<HTMLDivElement>(null);
  const [following, setFollowing] = useState(true);
  const threadQuery = useQuery({
    ...queries.thread(run?.thread_id ?? ""),
    enabled: !!run,
  });
  const pending = useQuery(queries.pending(runId));
  const thread = threadQuery.data;
  useEffect(() => {
    const content = transcript.current;
    const viewport = content?.closest("[data-session-stage]");
    if (!content || !(viewport instanceof HTMLElement)) return;
    let follow = true;
    const onScroll = () => {
      follow =
        viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight <
        100;
      setFollowing(follow);
    };
    const observer = new ResizeObserver(() => {
      if (follow) viewport.scrollTop = viewport.scrollHeight;
    });
    observer.observe(content);
    viewport.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      observer.disconnect();
      viewport.removeEventListener("scroll", onScroll);
    };
  }, [!!run, !!thread, threadId]);

  const currentRun = useRun(thread?.current_run_id);
  const headRun = useRun(thread?.head_run_id);
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
  const interruptKey = useIdempotency();
  const interrupt = useMutation({
    mutationFn: () => {
      const body = {
        expected_thread_version: thread!.version,
        expected_run_version: run!.version,
      };
      return client.http
        .POST("/api/v1/runs/{run_id}/interrupt", {
          params: {
            path: { run_id: runId },
            header: commandHeaders(workspace.id, interruptKey.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: () => {
      interruptKey.reset();
      void invalidateConversation(cache, workspace.id, {
        sessionId,
        threadId,
        runId,
      });
    },
  });
  const retryKey = useIdempotency();
  function accepted(receipt: Schema["RunAcceptanceReceipt"]) {
    void invalidateConversation(
      cache,
      workspace.id,
      { sessionId, threadId, runId },
      {
        sessionId: receipt.session_id,
        threadId: receipt.thread_id,
        runId: receipt.run_id,
      },
    );
    navigate(runPath(basePath, receipt));
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
            <Timestamp value={run.created_at} relative />
          </span>
        </div>
        <div className={styles.inline}>
          <RunInspector run={run} />
          {!current &&
            run.status === "completed" &&
            currentRun.data &&
            !isActiveRun(currentRun.data.status) &&
            can("run.continue") && (
              <ContinueBranch run={run} thread={thread} accepted={accepted} />
            )}
          {current &&
            ["failed", "cancelled"].includes(run.status) &&
            can("run.retry") && (
              <Button
                size="sm"
                variant="outline"
                loading={retry.isPending}
                onClick={() => retry.mutate()}
                type="button"
              >
                <ArrowsClockwiseIcon size={13} />
                {t("Retry run")}
              </Button>
            )}
          {run.status === "completed" && can("run.fork") && (
            <ModalFrame
              trigger={
                <Button size="sm" variant="outline" type="button">
                  {<GitForkIcon size={13} />}
                  {t("Fork")}
                </Button>
              }
              size={"md"}
              title={t("Fork conversation")}
              description={t(
                "Start a new thread from this completed run with the same agent configuration.",
              )}
              closeLabel={t("Close")}
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
            </ModalFrame>
          )}
        </div>
      </header>
      <ErrorNotice
        error={
          runQuery.error ?? threadQuery.error ?? retry.error ?? interrupt.error
        }
      />
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
      <div className={styles.transcript} ref={transcript}>
        <HistoryTranscript runId={runId} />
        <article className={styles.inputMessage}>
          <strong>
            {t(run.input_kind === "feedback" ? "Feedback" : "Input")}
          </strong>
          <InputContent input={run.input} fallback={run.input_text} />
        </article>
        <PresentedItems
          items={live.items}
          runState={run.status}
          agentName={agent.data?.name}
          agentId={run.agent_id}
          agentImageUrl={agent.data?.image_url}
        />
        {!live.items.some(
          (item) =>
            item.kind === "text_message" &&
            item.role === "assistant" &&
            item.text,
        ) &&
          run.output_text && (
            <article className={styles.message}>
              <div className={styles.messageBody}>
                <strong>{agent.data?.name ?? t("Agent")}</strong>
                <MarkdownContent text={run.output_text} />
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
            <h3>{t("The agent could not finish this run")}</h3>
            <p>
              {t(
                "Your messages are saved. Review the details or retry this run.",
              )}
            </p>
            <DisclosureSection title={<>{t("Error details")}</>}>
              <JsonView value={run.failure} />
            </DisclosureSection>
          </section>
        )}
        {run.output != null && run.output !== run.output_text && (
          <DisclosureSection
            className={styles.structuredOutput}
            defaultOpen
            title={<>{t("Structured output")}</>}
          >
            <JsonView value={run.output} />
          </DisclosureSection>
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
      {!following && (
        <Button
          className={styles.jumpToLatest}
          size="sm"
          variant="outline"
          onClick={() => {
            const viewport = transcript.current?.closest(
              "[data-session-stage]",
            );
            viewport?.scrollTo({
              top: viewport.scrollHeight,
              behavior: "smooth",
            });
          }}
          type="button"
        >
          {<ArrowDownIcon size={14} />}
          {t("Jump to latest")}
        </Button>
      )}
      <div className={styles.composerDock}>
        <div className={styles.dockControls}>
          {steer && can("run.continue") && (
            <ChoiceField
              placeholder={t("Send mode")}
              value={steer ? mode : "message"}
              onValueChange={setMode}
              label={t("Send mode")}
              hideLabel
              options={[
                {
                  value: "message",
                  label: t(
                    active || waiting
                      ? "Queue next message"
                      : "Continue thread",
                  ),
                },
                ...(steer
                  ? [{ value: "steer", label: t("Steer current run") }]
                  : []),
              ]}
            />
          )}

          {current && active && can("run.interrupt") && (
            <Button
              size="sm"
              variant="ghost"
              loading={interrupt.isPending}
              onClick={() => interrupt.mutate()}
              type="button"
            >
              <SquareIcon size={13} />
              {t("Stop")}
            </Button>
          )}
        </div>
        {current && can("run.continue") && (
          <>
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
                  void invalidateConversation(cache, workspace.id, {
                    sessionId,
                    threadId,
                    runId,
                  });
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
                    void invalidateConversation(cache, workspace.id, {
                      sessionId,
                      threadId,
                    });
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
        )}
      </div>
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
