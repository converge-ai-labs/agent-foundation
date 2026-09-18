import { EarlierMessages } from "./earlier-messages";
import { Button, DisclosureSection } from "a13n-ui";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useParams } from "react-router";

import { ArrowDownIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { type Schema } from "../../shared/api";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { JsonView } from "../../shared/forms";
import { conversationQueries, isActiveRun } from "./api";
import styles from "./conversations.module.css";
import { HistoryTranscript } from "./history";
import { InputContent, PresentedItems } from "./items";
import { useLiveRun } from "./live";
import { MarkdownContent } from "../../shared/markdown";
import { useAgent } from "../agents/queries";
import { useRun } from "./queries";
import { RunControls } from "./run-controls";

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
    { workspace } = useWorkspace(),
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
      if (follow && !viewport.dataset.loadingEarlier)
        viewport.scrollTop = viewport.scrollHeight;
    });
    observer.observe(content);
    viewport.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      observer.disconnect();
      viewport.removeEventListener("scroll", onScroll);
    };
  }, [!!run, !!thread, threadId]);

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
  const active = isActiveRun(run.status);
  return (
    <div className={styles.run}>
      <header className={styles.runHeader}>
        <div>
          <StatePill state={run.status} />
          <span>
            <Timestamp value={run.created_at} relative />
          </span>
        </div>
      </header>
      <ErrorNotice error={runQuery.error ?? threadQuery.error} />
      {live.gap && (
        <p role="status" className={styles.notice}>
          {live.incomplete
            ? t(
                "Saved message history is incomplete. Some output is unavailable.",
              )
            : t(
                "Live replay resumed from saved messages. Earlier raw events may be unavailable.",
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
        {!live.hasEarlier && <HistoryTranscript runId={runId} />}
        <article className={styles.inputMessage}>
          <strong>
            {t(run.input_kind === "feedback" ? "Feedback" : "Input")}
          </strong>
          <InputContent input={run.input} fallback={run.input_text} />
        </article>
        <EarlierMessages {...live} />
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
              <p>{t("Your messages are saved. Review the error details.")}</p>
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
      <RunControls run={run} thread={thread} configuration={configuration} />
    </div>
  );
}
