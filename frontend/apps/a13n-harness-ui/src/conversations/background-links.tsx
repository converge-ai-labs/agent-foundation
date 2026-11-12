import { useTranslation } from "../i18n";
import { useBackground } from "./background-context";
import { executionReceipt } from "./background-work";
import type { ToolView } from "./tool-presentation";
import styles from "./background.module.css";

export function executionHeaders(text: string) {
  return [
    ...text.matchAll(
      /(Subagent message|Subagent result|Tool notification|Tool result) · (.*?) · execution_id=([^\s·]+) · ([^\s]+)/g,
    ),
  ].map((match) => ({
    kind: match[1],
    name: match[2],
    id: match[3],
    state: match[4],
  }));
}
export function useNoticeLabel() {
  const { t } = useTranslation();
  const labels: Record<string, string> = {
    "Subagent message": t("Subagent message"),
    "Subagent result": t("Subagent result"),
    "Tool notification": t("Tool notification"),
    "Tool result": t("Tool result"),
  };
  return (text: string, source: string) =>
    labels[executionHeaders(text)[0]?.kind] ??
    (source === "tool_execution"
      ? t("Tool notification")
      : source === "background_process"
        ? t("Process update")
        : t("Subagent update"));
}
export function ExecutionNoticeLinks({ text }: { text: string }) {
  const bg = useBackground();
  const { t } = useTranslation();
  return executionHeaders(text).map((header) => {
    const target = bg.resolve(header.id);
    return target ? (
      <button
        key={header.id}
        type="button"
        className={styles.noticeLink}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          bg.open(target);
        }}
      >
        {t("Open {{name}}", { name: target.title })}
      </button>
    ) : null;
  });
}
export function MovedToBackground({ tool }: { tool: ToolView }) {
  const { t } = useTranslation();
  const bg = useBackground();
  const receipt = executionReceipt(tool);
  const item = bg.items.find(
    (item) =>
      item.tool?.execution_id === receipt?.execution_id ||
      (item.tool?.tool_call_id != null &&
        item.tool.tool_call_id === tool.toolCallId),
  );
  if (
    receipt?.type !== "tool_execution_receipt" &&
    !item?.tool?.foreground_released
  )
    return null;
  return item ? (
    <button
      type="button"
      className={styles.noticeLink}
      onClick={() => bg.open(item.target)}
    >
      {t("Moved to background")}
    </button>
  ) : (
    <span className={styles.noticeLink}>{t("Moved to background")}</span>
  );
}

export function splitExecutionNotices(text: string) {
  const headers = [
    ...text.matchAll(
      /^(?:Subagent message|Subagent result|Tool notification|Tool result) · .*? · execution_id=[^\s·]+ · [^\s]+/gm,
    ),
  ];
  if (headers.length < 2) return [text];
  return headers.map((header, index) =>
    text
      .slice(index === 0 ? 0 : header.index, headers[index + 1]?.index)
      .trim(),
  );
}
