import { Button, DisclosureSection } from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";

import { ArrowDownIcon, SquareIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import {
  ErrorNotice,
  ErrorToast,
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
import styles from "./conversations.module.css";
import { HistoryTranscript } from "./history";
import { InputContent, PresentedItems } from "./items";
import { useLiveRun } from "./live";
import { MarkdownContent } from "../../shared/markdown";
import { useAgent } from "../agents/queries";
import { useRun } from "./queries";
import { ThreadQueue } from "./queue";
import { ConfigurationFeedback } from "../configuration-assistant/feedback";
import { ContinueWithoutFeedback } from "../configuration-assistant/continuation";

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
export function RunContent({
  runId,
  threadId,
  sessionId,
  configuration,
}: {
  runId: string;
  threadId: string;
  sessionId: string;
  configuration?: {
    composer: ReactNode;
    accepted: (receipt: Schema["RunAcceptanceReceipt"]) => void;
  };
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can, basePath } = useWorkspace(),
    cache = useQueryClient(),
    queries = conversationQueries(client, workspace.id);
  const live = useLiveRun(runId);
  const runQuery = useRun(runId);
  const run = runQuery.data;
  const agent = useAgent(configuration ? undefined : run?.agent_id);
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
  const retry = useMutation({
    mutationFn: () => {
      const body = {
        expected_thread_version: thread!.version,
      };
      return client.http
        .POST("/api/v1/runs/{run_id}/retry", {
          params: {
            path: { run_id: runId },
            header: commandHeaders(workspace.id, retryKey.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: (receipt) => {
      retryKey.reset();
      configuration?.accepted(receipt);
    },
  });
  if (runQuery.isPending || threadQuery.isPending)
    return <Loading variant="detail" />;
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
  return (
    <div className={styles.run}>
      <header className={styles.runHeader}>
        <div>
          <StateBadge state={run.status} />
          <span>
            <Timestamp value={run.created_at} relative />
          </span>
        </div>
      </header>
      <ErrorNotice error={runQuery.error ?? threadQuery.error} />
      <ErrorToast error={interrupt.error ?? retry.error} />
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
          agentName={
            configuration ? t("Configuration assistant") : agent.data?.name
          }
          agentId={run.agent_id}
          agentImageUrl={agent.data?.image_url}
        >
          {!live.items.some(
            (item) =>
              item.kind === "text_message" &&
              item.role === "assistant" &&
              item.text,
          ) &&
            run.output_text && <MarkdownContent text={run.output_text} />}
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
        </PresentedItems>
      </div>
      {waiting && run.sealed_state_digest_sha256 && (
        <>
          <ErrorNotice error={pending.error} />
          {pending.data && (
            <ConfigurationFeedback
              key={run.sealed_state_digest_sha256}
              run={run}
              thread={thread}
              actions={pending.data.items}
              accepted={(receipt) => {
                configuration?.accepted(receipt);
                void invalidateConversation(cache, workspace.id, {
                  sessionId,
                  threadId,
                  runId,
                });
              }}
            />
          )}
          {configuration && can("run.continue") && can("run.feedback") && (
            <ContinueWithoutFeedback
              run={run}
              thread={thread}
              accepted={configuration.accepted}
            />
          )}
        </>
      )}
      {!following && (
        <Button
          className={styles.jumpToLatest}
          data-external-composer={configuration?.composer === null || undefined}
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
      {configuration &&
        current &&
        ["failed", "cancelled"].includes(run.status) &&
        can("run.retry") && (
          <Button
            size="sm"
            variant="outline"
            loading={retry.isPending}
            onClick={() => retry.mutate()}
            type="button"
          >
            {t("Retry run")}
          </Button>
        )}
      {((current && active && can("run.interrupt")) || !current) && (
        <div
          className={styles.composerDock}
          data-floating-controls={(current && active) || undefined}
        >
          {current && active && can("run.interrupt") && (
            <div className={styles.dockControls}>
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
            </div>
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
      )}
      {configuration?.composer}
      {!configuration && (
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
      )}
    </div>
  );
}
