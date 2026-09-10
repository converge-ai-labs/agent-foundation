import { Button, DisclosureSection } from "a13n-ui";

import { useState } from "react";

import {
  HeartIcon,
  BrainIcon,
  CheckIcon,
  CopyIcon,
  FileIcon,
  CircleNotchIcon,
  WrenchIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { StateBadge } from "../../shared/feedback";
import { JsonView } from "../../shared/form";
import styles from "./conversations.module.css";
import { MessageMarkdown } from "./markdown";
import { isObject, type PresentedItem } from "./projection";
export function PresentedItems({
  items,
  runState,
  agentName,
}: {
  items: readonly PresentedItem[];
  runState?: string;
  agentName?: string;
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
                <strong>{item.toolName || t("Tool call")}</strong>
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
            {item.text && <MessageMarkdown text={item.text} />}
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
          <div className={styles.messageAvatar}>{<HeartIcon size={16} />}</div>
          <div className={styles.messageBody}>
            <div className={styles.messageHeading}>
              <strong>{agentName ?? t("Agent")}</strong>
              {item.state !== "completed" && <StateBadge state={item.state} />}
            </div>
            <MessageMarkdown
              text={
                item.text ||
                (item.state === "streaming"
                  ? t("Thinking…")
                  : t("No text content"))
              }
            />
            {item.text && item.state === "completed" && (
              <CopyMessage text={item.text} />
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
      <div className={styles.prose}>
        {ordinary.content
          .flatMap((block) =>
            isObject(block) &&
            block.type === "text" &&
            typeof block.text === "string"
              ? [block.text]
              : [],
          )
          .join("\n\n") || fallback}
      </div>
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

function CopyMessage({ text }: { text: string }) {
  const { t } = useTranslation();
  const [status, setStatus] = useState("idle");
  return (
    <div className={styles.messageActions}>
      <Button
        type="button"
        variant="ghost"
        aria-label={t(status === "copied" ? "Copied" : "Copy message")}
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(text);
            setStatus("copied");
          } catch {
            setStatus("failed");
          }
        }}
        size="icon-sm"
      >
        {status === "copied" ? <CheckIcon size={13} /> : <CopyIcon size={13} />}
      </Button>
      <span role="status">
        {status === "copied"
          ? t("Copied")
          : status === "failed"
            ? t("Could not copy. Select the message to copy it manually.")
            : ""}
      </span>
    </div>
  );
}
