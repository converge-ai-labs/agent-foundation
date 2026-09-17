import type { ReactNode } from "react";
import { DisclosureSection } from "a13n-ui";

import {
  BrainIcon,
  CheckIcon,
  FileIcon,
  CircleNotchIcon,
  WrenchIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { StateBadge } from "../../shared/feedback";
import { JsonView } from "../../shared/form";
import styles from "./conversations.module.css";
import { MarkdownContent } from "../../shared/markdown";
import { isObject, type PresentedItem } from "./projection";
import { inputText } from "./input";
import { AssetAttachment } from "./attachment";
import { ToolDetails } from "./tool-details";
import { AgentAvatar } from "../agents/avatar";
import { CopyButton } from "../../shared/copy";
export function PresentedItems({
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
  const content = items
    .filter(
      (item) =>
        item.display !== false &&
        item.kind !== "run_output" &&
        (item.kind !== "text_message" || item.role === "assistant"),
    )
    .map((item) => {
      if (item.kind === "tool_call")
        return (
          <DisclosureSection
            key={item.id}
            className={styles.tool}
            title={
              <span className={styles.disclosureTitle}>
                <WrenchIcon size={14} />
                <strong title={item.toolName}>
                  {item.toolName || t("Tool call")}
                </strong>
                <span className={styles.toolState}>
                  {item.state === "completed" ? (
                    <CheckIcon size={13} aria-label={t("Completed")} />
                  ) : item.state === "in_progress" &&
                    ["running", "queued"].includes(runState ?? "running") ? (
                    <CircleNotchIcon
                      size={13}
                      className={styles.spinning}
                      aria-label={t("Working")}
                    />
                  ) : (
                    <StateBadge
                      state={
                        item.state === "in_progress"
                          ? ["waiting", "failed", "cancelled"].includes(
                              runState ?? "",
                            )
                            ? runState === "waiting"
                              ? "waiting"
                              : "interrupted"
                            : "unknown"
                          : item.state
                      }
                    />
                  )}
                </span>
              </span>
            }
          >
            <ToolDetails item={item} />
          </DisclosureSection>
        );
      if (item.kind === "reasoning_message")
        return (
          <DisclosureSection
            key={item.id}
            className={styles.reasoning}
            title={
              <span className={styles.disclosureTitle}>
                <BrainIcon size={14} />
                {t("Reasoning summary")}
                <StateBadge state={item.state} />
              </span>
            }
          >
            {item.text && <MarkdownContent text={item.text} />}
            {item.protectedReasoning && (
              <p>{t("The provider retained protected reasoning content.")}</p>
            )}
          </DisclosureSection>
        );
      if (item.kind !== "text_message")
        return (
          <DisclosureSection
            key={item.id}
            className={styles.tool}
            title={
              <>
                {t("Additional run item")}: {item.kind}
              </>
            }
          >
            <JsonView value={item.detail} />
          </DisclosureSection>
        );
      return (
        <article key={item.id} className={styles.message} data-role={item.role}>
          <MarkdownContent
            text={
              item.text ||
              (item.state === "in_progress"
                ? t("Thinking…")
                : t("No text content"))
            }
          />
          {item.text && item.state === "completed" && (
            <div className={styles.messageActions}>
              <CopyButton
                value={item.text}
                iconOnly
                copyLabel={t("Copy message")}
              />
            </div>
          )}
          {item.failure !== undefined && <JsonView value={item.failure} />}
        </article>
      );
    });
  if (content.length === 0 && !children) return null;
  return (
    <section className={styles.agentResponse} aria-label={t("Agent response")}>
      <div className={styles.agentHeading}>
        <AgentAvatar
          name={agentName ?? t("Agent")}
          id={agentId}
          url={agentImageUrl}
          className={styles.messageAvatar}
        />
        <strong>{agentName ?? t("Agent")}</strong>
      </div>
      <div className={styles.agentContent}>
        {content}
        {children}
      </div>
    </section>
  );
}
export function InputContent({
  input,
  fallback,
}: {
  input: unknown;
  fallback?: string | null;
}) {
  const { t } = useTranslation();
  if (isObject(input) && Array.isArray(input.resolutions))
    return (
      <div className={styles.prose}>
        {input.resolutions.map((resolution, index) =>
          isObject(resolution) ? (
            <section key={index}>
              <strong>{String(resolution.call_id ?? t("Response"))}</strong> ·{" "}
              {t(String(resolution.outcome ?? resolution.kind ?? "Response"))}
              {resolution.result !== undefined && (
                <JsonView value={resolution.result} />
              )}
              {resolution.response !== undefined && (
                <JsonView value={resolution.response} />
              )}
            </section>
          ) : null,
        )}
      </div>
    );
  const ordinary =
    isObject(input) && isObject(input.input) ? input.input : input;
  if (!isObject(ordinary) || !Array.isArray(ordinary.content))
    return fallback ? (
      <div className={styles.prose}>{fallback}</div>
    ) : (
      <JsonView value={input} />
    );
  return (
    <>
      <div className={styles.prose}>{inputText(input, fallback)}</div>
      <div className={styles.attachments}>
        {ordinary.content.flatMap((block, index) => {
          if (
            !isObject(block) ||
            block.type !== "binary" ||
            !isObject(block.source)
          )
            return [];
          const label =
            typeof block.filename === "string"
              ? block.filename
              : block.source.type === "asset"
                ? String(block.source.asset_id)
                : block.source.type === "url"
                  ? String(block.source.url)
                  : String(block.source.path);
          if (
            block.source.type === "asset" &&
            typeof block.source.asset_id === "string"
          )
            return [
              <AssetAttachment
                key={index}
                assetId={block.source.asset_id}
                filename={
                  typeof block.filename === "string"
                    ? block.filename
                    : undefined
                }
              />,
            ];
          return [
            <span key={index} className={styles.attachment}>
              <FileIcon size={13} />
              {label}
            </span>,
          ];
        })}
      </div>
      {ordinary.structured_content !== undefined &&
        ordinary.structured_content !== null && (
          <DisclosureSection title={<>{t("Structured input")}</>}>
            <JsonView value={ordinary.structured_content} />
          </DisclosureSection>
        )}
    </>
  );
}
