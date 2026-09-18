import { Button, DisclosureSection } from "a13n-ui";
import {
  ChatCircleTextIcon,
  RobotIcon,
  UserIcon,
  WrenchIcon,
  GearSixIcon,
} from "@phosphor-icons/react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { CopyButton } from "../../shared/identity";
import { MarkdownContent } from "../../shared/markdown";
import styles from "./traces.module.css";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Decode JSON containers without losing large integers in retained content. */
export function decodeContent(value: unknown): unknown {
  if (typeof value !== "string" || !/^[\s]*[\[{]/.test(value)) return value;
  try {
    let unsafeNumber = false;
    const parsed: unknown = JSON.parse(value, (_key, item: unknown) => {
      if (
        typeof item === "number" &&
        (!Number.isFinite(item) ||
          (Number.isInteger(item) && !Number.isSafeInteger(item)))
      )
        unsafeNumber = true;
      return item;
    });
    return unsafeNumber ? value : parsed;
  } catch {
    return value;
  }
}

function CodeText({ text, language }: { text: string; language: string }) {
  if (text.length > 100_000)
    return <pre className={`${styles.rawContent} a13n-scrollbar`}>{text}</pre>;
  // A longer fence keeps embedded Markdown fences inert inside source text.
  let fenceLength = 3;
  for (const match of text.matchAll(/`+/g))
    fenceLength = Math.max(fenceLength, match[0].length + 1);
  const fence = "`".repeat(fenceLength);
  return <MarkdownContent text={`${fence}${language}\n${text}\n${fence}`} />;
}

export function TraceJson({ value }: { value: unknown }) {
  return (
    <CodeText text={JSON.stringify(value, null, 2) ?? "null"} language="json" />
  );
}

function TraceText({ text }: { text: string }) {
  const trimmed = text.trim();
  // Producer context envelopes are source, not HTML to hide or execute.
  if (/^<([a-z][\w-]*)(?:\s[^>]*)?>[\s\S]*<\/\1>$/i.test(trimmed))
    return <CodeText text={text} language="xml" />;
  // Some producer messages have a prose heading followed by one JSON container.
  const newline = text.indexOf("\n");
  if (newline > 0 && !text.slice(0, newline).includes("`")) {
    const tail = text.slice(newline + 1);
    const parsed = decodeContent(tail);
    if (typeof parsed !== "string")
      return (
        <div className={styles.contentSequence}>
          <MarkdownContent text={text.slice(0, newline)} literalHtml />
          <TraceJson value={parsed} />
        </div>
      );
  }
  return <MarkdownContent text={text} literalHtml />;
}

export function TraceContent({
  content,
  compact = false,
}: {
  content: Schema["Content"] | null;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const [raw, setRaw] = useState(false);
  if (content === null)
    return (
      <p className={styles.contentEmpty}>
        {t(compact ? "Content omitted in Compact view." : "-")}
      </p>
    );
  return (
    <div className={styles.content}>
      <div className={styles.contentToolbar}>
        <span>{content.media_type ?? t("Retained content")}</span>
        <div>
          <Button
            size="sm"
            variant="ghost"
            aria-pressed={raw}
            onClick={() => setRaw(!raw)}
          >
            {t(raw ? "Preview" : "Raw JSON")}
          </Button>
          <CopyButton
            value={JSON.stringify(content, null, 2)}
            iconOnly
            copyLabel={t("Copy retained content")}
          />
        </div>
      </div>
      <div className={`${styles.contentBody} a13n-scrollbar`}>
        {raw ? (
          <TraceJson value={content} />
        ) : (
          <RichValue value={content.value} />
        )}
      </div>
    </div>
  );
}

function ExtraFields({
  value,
  omit,
}: {
  value: Record<string, unknown>;
  omit: string[];
}) {
  const { t } = useTranslation();
  const extra = Object.fromEntries(
    Object.entries(value).filter(([key]) => !omit.includes(key)),
  );
  if (!Object.keys(extra).length) return null;
  return (
    <DisclosureSection title={<>{t("Additional fields")}</>}>
      <TraceJson value={extra} />
    </DisclosureSection>
  );
}

const roleIcons = {
  user: UserIcon,
  assistant: RobotIcon,
  tool: WrenchIcon,
  system: GearSixIcon,
  developer: GearSixIcon,
};
const roleLabels = {
  user: "User",
  assistant: "Assistant",
  tool: "Tool",
  system: "System",
  developer: "Developer",
};

function RichValue({
  value: source,
  depth = 0,
}: {
  value: unknown;
  depth?: number;
}) {
  const { t } = useTranslation();
  const value = decodeContent(source);
  if (depth > 8) return <TraceJson value={value} />;
  if (typeof value === "string")
    return value ? (
      <TraceText text={value} />
    ) : (
      <p className={styles.contentEmpty}>{t("Empty text")}</p>
    );
  if (Array.isArray(value)) {
    // Render semantic sequences only. Arbitrary JSON arrays remain inspectable JSON.
    if (
      value.length &&
      value.every(
        (item) =>
          isRecord(item) &&
          (typeof item.role === "string" || typeof item.type === "string"),
      )
    )
      return (
        <div className={styles.contentSequence}>
          {value.map((item, index) => (
            <RichValue key={index} value={item} depth={depth + 1} />
          ))}
        </div>
      );
    return <TraceJson value={value} />;
  }
  if (!isRecord(value)) return <TraceJson value={value} />;
  const next = (item: unknown) => <RichValue value={item} depth={depth + 1} />;
  if (Array.isArray(value.messages))
    return (
      <div className={styles.contentSequence}>
        {next(value.messages)}
        {Array.isArray(value.tools) && (
          <DisclosureSection
            title={
              <>
                {t("Tool definitions")} · {value.tools.length}
              </>
            }
          >
            <TraceJson value={value.tools} />
          </DisclosureSection>
        )}
        <ExtraFields
          value={value}
          omit={
            Array.isArray(value.tools) ? ["messages", "tools"] : ["messages"]
          }
        />
      </div>
    );
  if (typeof value.role === "string") {
    const role = value.role;
    const knownRole = Object.hasOwn(roleIcons, role)
      ? (role as keyof typeof roleIcons)
      : null;
    const Icon = knownRole ? roleIcons[knownRole] : ChatCircleTextIcon;
    return (
      <article className={styles.message} data-role={role}>
        <header>
          <Icon size={16} />
          <strong>{knownRole ? t(roleLabels[knownRole]) : role}</strong>
          {typeof value.name === "string" && <span>{value.name}</span>}
        </header>
        <div className={styles.messageContent}>
          {"parts" in value
            ? next(value.parts)
            : "content" in value
              ? next(value.content)
              : null}
          {Array.isArray(value.tool_calls) &&
            value.tool_calls.map((call, index) => (
              <ToolContent key={index} value={call} depth={depth + 1} />
            ))}
          <ExtraFields
            value={value}
            omit={[
              "role",
              "name",
              "parts" in value ? "parts" : "content",
              ...(Array.isArray(value.tool_calls) ? ["tool_calls"] : []),
            ]}
          />
        </div>
      </article>
    );
  }
  if (
    [
      "tool_call",
      "function_call",
      "tool_use",
      "tool_call_response",
      "tool_result",
      "function_call_output",
    ].includes(String(value.type))
  )
    return <ToolContent value={value} depth={depth + 1} />;
  if (["text", "input_text", "output_text"].includes(String(value.type))) {
    const key =
      typeof value.text === "string"
        ? "text"
        : typeof value.content === "string"
          ? "content"
          : null;
    if (key)
      return (
        <div>
          {next(value[key])}
          <ExtraFields value={value} omit={["type", key]} />
        </div>
      );
  }
  if (
    "content" in value &&
    (Array.isArray(value.content) || typeof value.content === "string") &&
    !value.type
  )
    return (
      <div className={styles.contentSequence}>
        {next(value.content)}
        <ExtraFields value={value} omit={["content"]} />
      </div>
    );
  // Unknown types (including media) remain visible without loading external URLs.
  return <TraceJson value={value} />;
}

function ToolContent({ value, depth }: { value: unknown; depth: number }) {
  const { t } = useTranslation();
  if (!isRecord(value)) return <TraceJson value={value} />;
  const call = isRecord(value.function) ? value.function : value;
  const isResult = [
    "tool_call_response",
    "tool_result",
    "function_call_output",
  ].includes(String(value.type));
  const key = isResult
    ? "result" in value
      ? "result"
      : "output" in value
        ? "output"
        : "content"
    : "arguments" in call
      ? "arguments"
      : "input";
  return (
    <section className={styles.toolContent}>
      <header>
        <WrenchIcon size={15} />
        <strong>
          {typeof call.name === "string"
            ? call.name
            : t(isResult ? "Tool result" : "Tool call")}
        </strong>
        <span>{t(isResult ? "Result" : "Arguments")}</span>
      </header>
      {"id" in value && typeof value.id === "string" && (
        <div className={styles.identifier}>{value.id}</div>
      )}
      {key in call && <RichValue value={call[key]} depth={depth + 1} />}
      <ExtraFields value={call} omit={["type", "name", "id", key]} />
      {call !== value && (
        <ExtraFields value={value} omit={["type", "id", "function"]} />
      )}
    </section>
  );
}
