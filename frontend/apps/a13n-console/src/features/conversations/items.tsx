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
import { AgentAvatar } from "../agents/avatar";
import { CopyableId, CopyButton } from "../../shared/copy";
export function PresentedItems({
  items,
  runState,
  agentName,
  agentId,
  agentImageUrl,
}: {
  items: readonly PresentedItem[];
  runState?: string;
  agentName?: string;
  agentId?: string;
  agentImageUrl?: string | null;
}) {
  const { t } = useTranslation();
  return items
    .filter(
      (item) =>
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
                  ) : item.state === "streaming" && runState !== "waiting" ? (
                    <CircleNotchIcon
                      size={13}
                      className={styles.spinning}
                      aria-label={t("Working")}
                    />
                  ) : (
                    <StateBadge
                      state={runState === "waiting" ? "waiting" : item.state}
                    />
                  )}
                </span>
              </span>
            }
          >
            <div className={styles.toolBody}>
              <CopyableId value={item.toolName || item.id} />
              <h4>{t("Arguments")}</h4>
              <JsonView value={parseJson(item.arguments)} />
              {item.result !== undefined && (
                <>
                  <h4>{t("Result")}</h4>
                  <JsonView value={parseJson(item.result)} />
                </>
              )}
              {item.failure !== undefined && <JsonView value={item.failure} />}
            </div>
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
          <AgentAvatar
            name={agentName ?? t("Agent")}
            id={agentId}
            url={agentImageUrl}
            className={styles.messageAvatar}
          />
          <div className={styles.messageBody}>
            <div className={styles.messageHeading}>
              <strong>{agentName ?? t("Agent")}</strong>
              {item.state !== "completed" && <StateBadge state={item.state} />}
            </div>
            <MarkdownContent
              text={
                item.text ||
                (item.state === "streaming"
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
          </div>
        </article>
      );
    });
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
function parseJson(value: unknown) {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}
