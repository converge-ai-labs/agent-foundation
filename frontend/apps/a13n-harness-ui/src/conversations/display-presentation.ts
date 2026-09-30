import type { DisplayBlock as SharedBlock } from "a13n-ui/display";
import type { Schema } from "../transport/client";
import type { DisplayBlock } from "./stream";
import { record, sourceText, type AppliedEdit } from "./tool-presentation";

const text = (value: unknown) => (typeof value === "string" ? value : "");

/** Join reduced context facts without interpreting raw lifecycle events. */
export function contextPeerId(block: SharedBlock): string | undefined {
  const value =
    block.kind === "context_summary"
      ? block.content
      : block.content.name === "a13n.display.context_operation" &&
          record(block.content.value)
        ? block.content.value
        : undefined;
  if (typeof value?.operation_id !== "string") return;
  return `${block.scope_id}:${block.kind === "context_summary" ? "execution" : "context"}:${value.operation_id}`;
}

/** Rendering only: statuses, native tool pairing and summaries are producer facts. */
export function displayBlock(
  block: SharedBlock,
  peer?: SharedBlock,
): DisplayBlock | undefined {
  const c = block.content;
  const metadata = record(c.metadata) ? c.metadata : {};
  if (metadata.display === false) return;
  const done = !["pending", "running"].includes(block.status);
  const base = { id: block.id, text: text(c.text), done, metadata };
  switch (block.kind) {
    case "input":
      return { ...base, kind: "user" };
    case "text":
      return { ...base, kind: "assistant" };
    case "reasoning":
      return { ...base, kind: "thinking" };
    case "media":
      return { ...base, kind: "media", value: c.media };
    case "context_summary": {
      if (peer) return; // The lifecycle row owns the joined presentation.
      const complete = c.kind === "provider" && block.status === "succeeded";
      return {
        ...base,
        kind: "activity",
        context: c.kind === "handoff" ? "handoff" : "compaction",
        name: c.kind === "handoff" ? "Summary" : "Compact Summary",
        done: complete,
        result: complete ? text(c.text) : undefined,
        text: complete ? "Summary ready" : "running",
      };
    }
    case "tool_chunk": {
      const outcome = c.outcome;
      const edit = metadata["a13n.harness-ui.applied_edit"];
      return {
        ...base,
        kind: "tool",
        toolCallId: text(c.tool_call_id),
        name: text(c.name),
        text: sourceText(c.arguments),
        done: c.arguments_complete === true,
        result: "result" in c ? sourceText(c.result) : undefined,
        outcome: ["success", "failed", "denied", "interrupted"].includes(
          text(outcome),
        )
          ? (outcome as DisplayBlock["outcome"])
          : block.status === "failed"
            ? "failed"
            : block.status === "cancelled"
              ? "interrupted"
              : "result" in c
                ? "success"
                : undefined,
        retry: c.retry === true,
        failure: c.retry === true ? sourceText(c.result) : undefined,
        stopped: block.status === "cancelled" || block.status === "unknown",
        provider: c.native ? text(c.provider) || "native" : undefined,
        edit:
          record(edit) &&
          typeof edit.file_path === "string" &&
          typeof edit.before === "string" &&
          typeof edit.after === "string"
            ? (edit as AppliedEdit)
            : undefined,
        images: Array.isArray(metadata["a13n.harness-ui.tool_images"])
          ? (metadata[
              "a13n.harness-ui.tool_images"
            ] as Schema<"ToolImageView">[])
          : undefined,
        apps: Array.isArray(metadata["a13n.harness-ui.mcp_apps"])
          ? (metadata["a13n.harness-ui.mcp_apps"] as Schema<"AppReference">[])
          : undefined,
        imageUnavailable:
          metadata["a13n.harness-ui.tool_image_unavailable"] === true,
      };
    }
    case "extension": {
      const value = record(c.value) ? c.value : {};
      if (c.name === "a13n.display.context_operation")
        return {
          ...base,
          kind: "activity",
          context: value.operation === "handoff" ? "handoff" : "compaction",
          name: value.operation === "handoff" ? "Summary" : "Compact Summary",
          text: [block.status, text(value.error_code), text(value.reason)]
            .filter(Boolean)
            .join(" · "),
          result:
            block.status === "succeeded"
              ? [
                  text(peer?.content.text) || "Summary content unavailable.",
                  Array.isArray(peer?.content.files) &&
                  peer.content.files.length
                    ? `Files to inspect:\n${peer.content.files.filter((item) => typeof item === "string").join("\n")}`
                    : "",
                ]
                  .filter(Boolean)
                  .join("\n\n")
              : undefined,
        };
      if (c.name === "a13n.display.task")
        return {
          ...base,
          kind: "task",
          name: text(value.status),
          text: text(
            value.status === "in_progress"
              ? value.active_form || value.subject
              : value.subject,
          ),
          result: [
            text(value.owner),
            Array.isArray(value.blocked_by) && value.blocked_by.length
              ? `Blocked by ${value.blocked_by.length} tasks`
              : "",
          ]
            .filter(Boolean)
            .join(" · "),
        };
      // Model request instrumentation is not a transcript row.
      return;
    }
  }
}
