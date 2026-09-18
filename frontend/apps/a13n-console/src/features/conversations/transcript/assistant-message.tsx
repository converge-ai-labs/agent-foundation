import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { CopyButton } from "../../../shared/identity";
import { JsonView } from "../../../shared/forms";
import { MarkdownContent } from "../../../shared/markdown";
import { AgentAvatar } from "../../agents/avatar";
import type { PresentedItem } from "../projection";
import { ExecutionGroup } from "./execution-group";
import { transcriptBlocks } from "./items";
import styles from "./transcript.module.css";

/**
 * One identity for the whole answer: the agent is named once, then its
 * reasoning, steps, and prose follow in the order they were observed.
 */
export function AgentTurn({
  items,
  runState,
  agentName,
  agentId,
  agentImageUrl,
  children,
}: {
  items: readonly PresentedItem[];
  runState?: string;
  agentName?: string;
  agentId?: string;
  agentImageUrl?: string | null;
  children?: ReactNode;
}) {
  const { t } = useTranslation();
  const blocks = transcriptBlocks(items);
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
      </div>
      <div className={styles.agentContent}>
        {blocks.map((block) =>
          block.kind === "execution" ? (
            <ExecutionGroup
              key={block.id}
              items={block.items}
              runState={runState}
            />
          ) : (
            <AgentMessage key={block.id} item={block.item} />
          ),
        )}
        {children}
      </div>
    </section>
  );
}

function AgentMessage({ item }: { item: PresentedItem }) {
  const { t } = useTranslation();
  return (
    <article
      className={styles.agentMessage}
      data-message-id={item.id}
      data-role={item.role}
    >
      <MarkdownContent
        text={
          item.text ||
          (item.state === "in_progress" ? t("Thinking…") : t("No text content"))
        }
      />
      {item.failure !== undefined && <JsonView value={item.failure} />}
      {item.text && item.state === "completed" && (
        <div className={styles.messageActions}>
          <CopyButton
            value={item.text}
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
