import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { CopyButton } from "../../../shared/identity";
import { JsonView } from "../../../shared/forms";
import { MarkdownContent } from "../../../shared/markdown";
import { AgentAvatar } from "../../agents/avatar";
import { formatDuration } from "../format";
import type { ContentEntry } from "../timeline";
import type { TranscriptBlock } from "./items";
import { GuidanceMessage } from "./user-message";
import { WorkLine } from "./work-line";
import styles from "./transcript.module.css";

/**
 * One identity for the whole answer: the agent is named once, then its work,
 * guidance it received, and its prose follow in the order they were observed.
 */
export function AgentTurn({
  blocks,
  runState,
  durationMs,
  agentName,
  agentId,
  agentImageUrl,
  childPath,
  children,
}: {
  blocks: readonly TranscriptBlock[];
  runState?: string;
  /** How long the run took, shown after the name once it completed. */
  durationMs?: number | null;
  agentName?: string;
  agentId?: string;
  agentImageUrl?: string | null;
  /** Where a delegated child thread can be opened, when one is resolvable. */
  childPath?: string | null;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  if (!blocks.length && !children) return null;
  // The name carries one fact: what the run is doing, or how long it took.
  const status =
    runState === "waiting"
      ? t("waiting for you")
      : ["running", "queued", "accepted"].includes(runState ?? "")
        ? t("working")
        : ["failed", "cancelled"].includes(runState ?? "")
          ? t("stopped")
          : durationMs != null
            ? formatDuration(durationMs)
            : null;
  return (
    <section className={styles.agentTurn} aria-label={t("Agent response")}>
      <div className={styles.agentHeading}>
        <AgentAvatar
          name={agentName ?? t("Agent")}
          id={agentId}
          url={agentImageUrl}
          className={styles.agentAvatar}
        />
        <strong>{agentName ?? t("Agent")}</strong>
        {status && (
          <span className={styles.agentStatus} data-state={runState}>
            {status}
          </span>
        )}
      </div>
      <div className={styles.agentContent}>
        {blocks.map((block) =>
          block.kind === "work" ? (
            <WorkLine
              key={block.id}
              entries={block.entries}
              childPath={childPath}
            />
          ) : block.kind === "guidance" ? (
            <GuidanceMessage key={block.id} text={block.entry.text} />
          ) : (
            <AgentMessage key={block.id} entry={block.entry} />
          ),
        )}
        {children}
      </div>
    </section>
  );
}

function AgentMessage({ entry }: { entry: ContentEntry }) {
  const { t } = useTranslation();
  const streaming = entry.state === "in_progress";
  return (
    <article
      className={styles.agentMessage}
      data-message-id={entry.id}
      data-role="assistant"
      data-streaming={streaming || undefined}
    >
      {entry.text ? (
        <MarkdownContent text={entry.text} />
      ) : (
        <p className={styles.thinking}>
          {streaming ? t("Thinking…") : t("No text content")}
        </p>
      )}
      {entry.failure != null && <JsonView value={entry.failure} />}
      {entry.text && entry.state === "completed" && (
        <div className={styles.messageActions}>
          <CopyButton
            value={entry.text}
            iconOnly
            copyLabel={t("Copy message")}
          />
        </div>
      )}
    </article>
  );
}

/** The agent is still moving: a quiet row in place of the next step. */
export function WorkingRow({ connected }: { connected: boolean }) {
  const { t } = useTranslation();
  return (
    <p className={styles.workingRow} role="status">
      <span className={styles.workingDot} aria-hidden="true" />
      {t(connected ? "Working…" : "Connecting to run…")}
    </p>
  );
}
