import { LogoAnimation } from "a13n-ui";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { CopyButton } from "../../../shared/identity";
import { JsonView } from "../../../shared/forms";
import { AgentAvatar } from "../../agents/avatar";
import { formatDuration } from "../format";
import type { ContentEntry } from "../timeline";
import type { TranscriptBlock } from "./items";
import {
  StoredContent,
  LegacyTruncation,
  useStoredReference,
} from "../stored-content";
import { GuidanceMessage } from "./user-message";
import { WorkLine } from "./work-line";
import styles from "./transcript.module.css";

/**
 * One identity for the whole answer: the agent is named once, then its work,
 * guidance it received, and its prose follow in the order they were observed,
 * all from the same left edge as the name's avatar.
 */
export function AgentTurn({
  blocks,
  durationMs,
  agentName,
  agentId,
  agentImageUrl,
  childPath,
  footer,
  children,
}: {
  blocks: readonly TranscriptBlock[];
  /** How long a completed run took; the transcript shows every other state. */
  durationMs?: number | null;
  agentName?: string;
  agentId?: string;
  agentImageUrl?: string | null;
  /** Where a delegated child thread can be opened, when one is resolvable. */
  childPath?: string | null;
  /** The quiet actions that close the answer. */
  footer?: ReactNode;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  if (!blocks.length && !children) return null;
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
        {durationMs != null && (
          <span className={styles.agentDuration}>
            {formatDuration(durationMs)}
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
            <GuidanceMessage
              sourceId={block.entry.sourceId}
              steeringSource={block.entry.steeringSource}
              key={block.id}
              text={block.entry.text}
              itemId={block.entry.id}
            />
          ) : (
            <AgentMessage key={block.id} entry={block.entry} />
          ),
        )}
        {children}
        {footer}
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
        <StoredContent itemId={entry.id} field="text" value={entry.text} />
      ) : (
        <p className={styles.thinking}>
          {streaming ? t("Thinking…") : t("No text content")}
        </p>
      )}
      {entry.failure != null && <JsonView value={entry.failure} />}
      <LegacyTruncation itemId={entry.id} />
    </article>
  );
}

/** The answer as a whole is copied from its last reply, once it is complete. */
export function CopyReply({ entry }: { entry: ContentEntry }) {
  const { t } = useTranslation();
  // A reply kept in storage is read whole from its own view, never from a preview.
  if (useStoredReference(entry.id, "text")) return null;
  return <CopyButton value={entry.text} iconOnly copyLabel={t("Copy reply")} />;
}

/** The agent is still moving: the mark turns in place of the next step. */
export function WorkingRow({ connected }: { connected: boolean }) {
  const { t } = useTranslation();
  return (
    <p className={styles.workingRow} role="status">
      <LogoAnimation size={16} />
      {t(connected ? "Working…" : "Connecting to run…")}
    </p>
  );
}
