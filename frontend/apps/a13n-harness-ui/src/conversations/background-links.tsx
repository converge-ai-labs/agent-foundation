import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "../i18n";
import { useTransport } from "../transport/context";
import { result } from "../transport/client";
import { useBackground } from "./background-context";
import { executionReceipt, type BackgroundTarget } from "./background-work";
import type { ToolView } from "./tool-presentation";
import { SubagentGlyph } from "./background-card";
import { Terminal, Wrench } from "@phosphor-icons/react";
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
  return (text: string, source: string, count = 1) => {
    const kind = labels[executionHeaders(text)[0]?.kind];
    if (count > 1)
      return kind ? `${kind} · ${count}` : t("System update count", { count });
    return (
      kind ??
      (source === "tool_execution"
        ? t("Tool notification")
        : source === "background_process"
          ? t("Process update")
          : t("Subagent update"))
    );
  };
}
export function ExecutionNoticeLinks({
  text,
  executionIds,
}: {
  text: string;
  executionIds?: unknown;
}) {
  const headers = executionHeaders(text);
  const refs = headers.length
    ? headers
    : Array.isArray(executionIds)
      ? executionIds
          .filter((id): id is string => typeof id === "string")
          .map((id) => ({ id, kind: "Subagent update" }))
      : [];
  return refs.map(({ id, kind }) => (
    <ExecutionNoticeLink
      key={id}
      id={id}
      childNotice={kind.startsWith("Subagent")}
    />
  ));
}
function ExecutionNoticeLink({
  id,
  childNotice,
}: {
  id: string;
  childNotice: boolean;
}) {
  const bg = useBackground();
  const { client } = useTransport();
  const local = bg.resolve(id);
  // Earlier segments of a resumed child are absent from the latest-per-child
  // list, so look them up by execution ID.
  const lookup = useQuery({
    queryKey: ["thread", bg.threadId, "child-review", id],
    enabled: !local && !bg.loading && childNotice,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/children", {
          params: {
            path: { thread_id: bg.threadId },
            query: { execution_id: id },
          },
          signal,
        }),
      ),
  });
  const child = lookup.data?.executions[0];
  const target: BackgroundTarget | undefined =
    local ??
    (child && {
      kind: "child",
      id: child.child_thread_id,
      executionId: child.execution_id,
      threadId: bg.threadId,
      title: child.subagent_name,
    });
  return target ? <BackgroundLink target={target} /> : null;
}

export function BackgroundLink({ target }: { target: BackgroundTarget }) {
  const bg = useBackground();
  return (
    <button
      type="button"
      className={styles.noticeLink}
      title={target.title}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        bg.open(target);
      }}
    >
      {target.kind === "child" ? (
        <SubagentGlyph id={target.id} />
      ) : target.icon === "terminal" ? (
        <Terminal size={14} />
      ) : (
        <Wrench size={14} />
      )}
      <span>{target.title}</span>
    </button>
  );
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
